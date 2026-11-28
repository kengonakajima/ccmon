#!/usr/bin/env python3
"""
ccmon の活動ログ（~/.ccmon/activity/YYYY-MM.jsonl）を解析し、
Mac操作・AI稼働・離席AI待ち・回収ラグ・アプリ別利用時間・9:00〜18:00稼働バランスを
テキストサマリー＆グラフィック（PNG/SVG）で出力するツール。

使い方:
    ./venv/bin/python3 report_activity.py                    # 今月分を集計＋画像出力
    ./venv/bin/python3 report_activity.py --month 2026-11    # 指定月
    ./venv/bin/python3 report_activity.py --days 7           # 直近7日間
    ./venv/bin/python3 report_activity.py --date 2026-11-28  # 指定日のみ
    ./venv/bin/python3 report_activity.py --meetings path.csv
    ./venv/bin/python3 report_activity.py --no-image         # テキスト出力のみ
"""
import argparse
import csv
import html
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_LOG_DIR = Path.home() / '.ccmon' / 'activity'
DEFAULT_OUT_DIR = Path.home() / '.ccmon' / 'reports'
FONT = 'Hiragino Sans, Hiragino Kaku Gothic ProN, sans-serif'
WD_NAMES = ['月', '火', '水', '木', '金', '土', '日']

# サンプル間ギャップがこれを超えたら Mac スリープまたは ccmon 停止とみなす（既定ハートビート5分＋余裕）
MAX_CONTINUOUS_GAP_S = 360.0
DEFAULT_TAIL_STEP_S = 10.0

# 5分類キーと表示設定
CATEGORIES = [
    ('human_only',   'Mac操作（AIなし）',       '#3B82F6'),
    ('human_and_ai', 'Mac操作＋AI並行',         '#0D9488'),
    ('away_ai',      '離席中・AI稼働待ち',      '#F97316'),
    ('away_waiting', '離席中・入力待ち(回収ラグ)', '#EAB308'),
    ('idle',         '離席・非稼働',            '#E2E8F0'),
]
CAT_COLORS = {k: col for k, _, col in CATEGORIES}


def fmt_hm(seconds: float) -> str:
    mins = int(round(seconds / 60.0))
    if mins <= 0:
        if seconds >= 1:
            return f'{int(round(seconds))}秒'
        return '0分'
    h, m = divmod(mins, 60)
    if h == 0:
        return f'{m}分'
    if m == 0:
        return f'{h}h'
    return f'{h}h{m:02d}m'


def fmt_jp(seconds: float) -> str:
    mins = int(round(seconds / 60.0))
    if mins <= 0:
        return f'{int(round(seconds))}秒' if seconds >= 1 else '0分'
    h, m = divmod(mins, 60)
    if h == 0:
        return f'{m}分'
    return f'{h}時間{m:02d}分'


def classify_record(rec: dict) -> str:
    """1レコードを排他的な5カテゴリに分類する。"""
    active = bool(rec.get('active'))
    agents = rec.get('agents') or []
    waiting = rec.get('waiting') or []
    if active and agents:
        return 'human_and_ai'
    if active:
        return 'human_only'
    if agents:
        return 'away_ai'
    if waiting:
        return 'away_waiting'
    return 'idle'


def load_records(log_dir: Path, start_date: date, end_date: date) -> list[dict]:
    """対象期間 [start_date, end_date]（ローカル日付）のレコードを時系列で読み込む。"""
    if not log_dir.exists():
        return []
    # 対象月の YYYY-MM.jsonl を列挙（前後1日境界のため開始月〜終了月）
    months = set()
    cur = start_date.replace(day=1)
    while cur <= end_date:
        months.add(cur.strftime('%Y-%m'))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)

    start_ts = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0).astimezone().timestamp()
    end_ts = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59).astimezone().timestamp() + 1.0

    records = []
    for ym in sorted(months):
        p = log_dir / f'{ym}.jsonl'
        if not p.exists():
            continue
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = rec.get('ts')
                if ts is None:
                    continue
                ts = float(ts)
                if start_ts <= ts < end_ts:
                    records.append(rec)
    records.sort(key=lambda r: float(r['ts']))
    return records


def load_meetings_csv(path: Optional[Path]) -> dict[str, list[tuple[float, float, str]]]:
    """会議CSV（日付,開始,終了,ツール）を読み込む。"""
    by_day = defaultdict(list)
    if not path or not path.exists():
        return by_day
    with open(path, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            ds = (row.get('日付') or '').strip()
            s = (row.get('開始') or '').strip()
            e = (row.get('終了') or '').strip()
            if not ds or not ds[0].isdigit() or ':' not in s or ':' not in e:
                continue
            sh, sm = map(int, s.split(':'))
            eh, em = map(int, e.split(':'))
            h0, h1 = sh + sm / 60.0, eh + em / 60.0
            if h1 > h0:
                by_day[ds].append((h0, h1, (row.get('ツール') or '').strip()))
    return by_day


def split_segment_by_local_day(t0: float, t1: float):
    """[t0, t1] をローカル日付ごとの (day_str, h0, h1, dur_s) に分割する。"""
    cur = t0
    while cur < t1:
        dt = datetime.fromtimestamp(cur).astimezone()
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day_end = day_start + 86400.0
        seg_end = min(t1, day_end)
        yield dt.strftime('%Y-%m-%d'), (cur - day_start) / 3600.0, (seg_end - day_start) / 3600.0, (seg_end - cur)
        cur = seg_end


def analyze_records(
    records: list[dict],
    start_date: date,
    end_date: date,
    *,
    max_gap_s: float = MAX_CONTINUOUS_GAP_S,
    tail_step_s: float = DEFAULT_TAIL_STEP_S,
) -> dict:
    """レコード列を区間集計し、日別・全体・アプリ別・エージェント別・9〜18時サマリーを返す。"""
    days = {}
    d = start_date
    while d <= end_date:
        ds = d.strftime('%Y-%m-%d')
        days[ds] = {
            'date': d,
            'cat_s': defaultdict(float),
            'cat_9to18_s': defaultdict(float),
            'app_active_s': defaultdict(float),
            'app_front_s': defaultdict(float),
            'agent_s': defaultdict(float),
            'segments': [],  # (h0, h1, cat)
        }
        d += timedelta(days=1)

    totals = {
        'cat_s': defaultdict(float),
        'cat_9to18_s': defaultdict(float),
        'app_active_s': defaultdict(float),
        'app_front_s': defaultdict(float),
        'agent_s': defaultdict(float),
        'recorded_s': 0.0,
        'sample_count': len(records),
    }

    n = len(records)
    for i, rec in enumerate(records):
        t0 = float(rec['ts'])
        if i + 1 < n:
            next_t = float(records[i + 1]['ts'])
            dt = next_t - t0
            if dt <= 0:
                continue
            # ギャップが max_gap_s 超ならスリープ/停止なので、このサンプル自体の末尾 tail_step_s だけ計上
            dur = dt if dt <= max_gap_s else min(tail_step_s, dt)
        else:
            dur = tail_step_s

        t1 = t0 + dur
        cat = classify_record(rec)
        app = rec.get('app') or '(不明)'
        agents = rec.get('agents') or []
        active = bool(rec.get('active'))

        for ds, h0, h1, seg_s in split_segment_by_local_day(t0, t1):
            if ds not in days:
                continue
            info = days[ds]
            info['cat_s'][cat] += seg_s
            totals['cat_s'][cat] += seg_s
            totals['recorded_s'] += seg_s

            # 9:00〜18:00 クリップ
            c0, c1 = max(h0, 9.0), min(h1, 18.0)
            if c1 > c0:
                s9 = (c1 - c0) * 3600.0
                info['cat_9to18_s'][cat] += s9
                totals['cat_9to18_s'][cat] += s9

            if active:
                info['app_active_s'][app] += seg_s
                totals['app_active_s'][app] += seg_s
            if cat != 'idle':
                info['app_front_s'][app] += seg_s
                totals['app_front_s'][app] += seg_s

            for ag in agents:
                info['agent_s'][ag] += seg_s
                totals['agent_s'][ag] += seg_s

            # 連続する同カテゴリのセグメントは結合して描画を軽量化
            if info['segments'] and info['segments'][-1][2] == cat and abs(info['segments'][-1][1] - h0) < 0.004:
                prev_h0, _, _ = info['segments'][-1]
                info['segments'][-1] = (prev_h0, h1, cat)
            else:
                info['segments'].append((h0, h1, cat))

    return {'days': days, 'totals': totals, 'start_date': start_date, 'end_date': end_date}


def print_text_report(result: dict):
    totals = result['totals']
    days = result['days']
    s_d, e_d = result['start_date'], result['end_date']
    n_days = len(days)

    print(f'=== ccmon 活動ログ集計 ({s_d} 〜 {e_d} / {n_days}日間) ===')
    print(f'サンプル数: {totals["sample_count"]}件 | 記録時間合計: {fmt_jp(totals["recorded_s"])}')
    if totals['recorded_s'] <= 0:
        print('対象期間の活動ログがありません。ccmon を起動してしばらく経ってから再実行してください。')
        return

    print('\n--- 時間配分（5分類・全時間帯） ---')
    for key, label, _ in CATEGORIES:
        sec = totals['cat_s'][key]
        pct = sec / totals['recorded_s'] * 100.0
        print(f'  {label:24s}: {fmt_jp(sec):>12s} ({pct:5.1f}%)')

    ai_total = totals['cat_s']['human_and_ai'] + totals['cat_s']['away_ai']
    mac_total = totals['cat_s']['human_only'] + totals['cat_s']['human_and_ai']
    print(f'  {"[合算] Mac操作合計":24s}: {fmt_jp(mac_total):>12s}')
    print(f'  {"[合算] AI稼働合計":24s}: {fmt_jp(ai_total):>12s}')

    # 9:00-18:00 summary
    slot_9to18 = n_days * 9 * 3600.0
    act_9to18 = (
        totals['cat_9to18_s']['human_only']
        + totals['cat_9to18_s']['human_and_ai']
        + totals['cat_9to18_s']['away_ai']
    )
    print(f'\n--- 9:00〜18:00 の稼働バランス（{n_days}日間 / 計{n_days*9}時間） ---')
    print(f'  Mac操作（AIなし＋並行）: {fmt_jp(totals["cat_9to18_s"]["human_only"] + totals["cat_9to18_s"]["human_and_ai"])}')
    print(f'  離席中・AI稼働待ち:     {fmt_jp(totals["cat_9to18_s"]["away_ai"])}')
    print(f'  離席中・回収ラグ:       {fmt_jp(totals["cat_9to18_s"]["away_waiting"])}')
    print(f'  9〜18時 稼働率(Mac+AI): {act_9to18 / slot_9to18 * 100:.1f}% (1日平均 {fmt_hm(act_9to18 / n_days)})')

    if totals['agent_s']:
        print('\n--- AIエージェント別 稼働時間 ---')
        for ag, sec in sorted(totals['agent_s'].items(), key=lambda x: x[1], reverse=True):
            print(f'  {ag:16s}: {fmt_jp(sec):>12s}')

    if totals['app_active_s']:
        print('\n--- 最前面アプリ別 操作時間（上位12件） ---')
        tot_app = sum(totals['app_active_s'].values()) or 1.0
        for app, sec in sorted(totals['app_active_s'].items(), key=lambda x: x[1], reverse=True)[:12]:
            print(f'  {app:24s}: {fmt_jp(sec):>12s} ({sec/tot_app*100:5.1f}%)')

    print('\n--- 日別サマリー ---')
    for ds in sorted(days.keys()):
        info = days[ds]
        d = info['date']
        wd = WD_NAMES[d.weekday()]
        mac_s = info['cat_s']['human_only'] + info['cat_s']['human_and_ai']
        ai_s = info['cat_s']['human_and_ai'] + info['cat_s']['away_ai']
        lag_s = info['cat_s']['away_waiting']
        if mac_s + ai_s + lag_s <= 0:
            continue
        print(f'  {ds}({wd}): Mac操作 {fmt_hm(mac_s):>7s} | AI稼働 {fmt_hm(ai_s):>7s} (内離席待ち {fmt_hm(info["cat_s"]["away_ai"]):>6s}) | 回収ラグ {fmt_hm(lag_s):>6s}')


def render_dashboard_svg(result: dict, out_svg: Path, meetings_by_day: Optional[dict] = None):
    """日別タイムライン＋アプリ別ランキング＋時間配分バーを1枚のSVGに出力する。"""
    days = result['days']
    totals = result['totals']
    meetings_by_day = meetings_by_day or {}
    sorted_ds = sorted(days.keys())
    n_days = len(sorted_ds)

    W = 1560
    PAD_X = 34
    HEADER_H = 128
    AXIS_H = 26
    ROW_H = 28 if n_days > 14 else 38
    TIMELINE_H = AXIS_H + n_days * ROW_H + 16
    PANEL_L_W = 1040
    PANEL_R_X = PAD_X + PANEL_L_W + 18
    PANEL_R_W = W - PAD_X - PANEL_R_X
    BODY_H = max(TIMELINE_H + 34, 460)
    FOOTER_H = 40
    H = HEADER_H + BODY_H + FOOTER_H

    mac_total = totals['cat_s']['human_only'] + totals['cat_s']['human_and_ai']
    ai_total = totals['cat_s']['human_and_ai'] + totals['cat_s']['away_ai']
    away_ai = totals['cat_s']['away_ai']
    lag_total = totals['cat_s']['away_waiting']

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f'<defs><style>text {{ font-family: {FONT}; }}</style></defs>',
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#F8FAFC"/>',
    ]

    # Header
    title_rng = f'{result["start_date"]} 〜 {result["end_date"]}'
    parts.append(f'<text x="{PAD_X}" y="42" font-size="24" font-weight="700" fill="#0F172A">ccmon 活動ログ 時間配分ダッシュボード</text>')
    parts.append(f'<text x="{PAD_X}" y="66" font-size="12.5" fill="#475569">対象期間: {title_rng}（{n_days}日間・サンプル {totals["sample_count"]}件）｜全AIエージェント・Mac操作・最前面アプリ統合</text>')

    # KPI cards
    kpis = [
        ('Mac操作合計', fmt_jp(mac_total), f'AI並行 {fmt_hm(totals["cat_s"]["human_and_ai"])}', '#2563EB', '#EFF6FF', '#93C5FD'),
        ('AI稼働合計', fmt_jp(ai_total), f'内離席待ち {fmt_hm(away_ai)}', '#EA580C', '#FFF7ED', '#FDBA74'),
        ('離席・回収ラグ', fmt_jp(lag_total), '完了後気づくまでの待機', '#CA8A04', '#FEFCE8', '#FDE047'),
        ('記録カバー時間', fmt_jp(totals['recorded_s']), 'スリープ/停止除く', '#475569', '#FFFFFF', '#CBD5E1'),
    ]
    bx = W - PAD_X
    bw = 182
    for lbl, val, sub, acc, bg, bd in reversed(kpis):
        bx -= bw
        parts.append(f'<rect x="{bx}" y="18" width="{bw}" height="64" rx="10" fill="{bg}" stroke="{bd}" stroke-width="1"/>')
        parts.append(f'<text x="{bx+11}" y="35" font-size="10.5" font-weight="600" fill="{acc}">{lbl}</text>')
        parts.append(f'<text x="{bx+11}" y="56" font-size="17" font-weight="700" fill="#0F172A">{val}</text>')
        parts.append(f'<text x="{bx+11}" y="72" font-size="9.8" fill="#64748B">{sub}</text>')
        bx -= 10

    # Legend
    ly = 108
    lx = PAD_X
    for key, lbl, col in CATEGORIES:
        parts.append(f'<rect x="{lx}" y="{ly-10}" width="14" height="10" rx="2" fill="{col}" stroke="#94A3B8" stroke-width="0.5"/>')
        parts.append(f'<text x="{lx+20}" y="{ly}" font-size="11.5" fill="#334155">{lbl}</text>')
        lx += 20 + len(lbl) * 11.8 + 18
    if meetings_by_day:
        parts.append(f'<rect x="{lx}" y="{ly-10}" width="14" height="10" rx="2" fill="#1D4ED8" fill-opacity="0.35" stroke="#1D4ED8" stroke-width="1"/>')
        parts.append(f'<text x="{lx+20}" y="{ly}" font-size="11.5" fill="#1D4ED8">会議(CSV)</text>')

    # Left Panel: Day-by-day 24h timeline
    p1_y = HEADER_H
    parts.append(f'<rect x="{PAD_X}" y="{p1_y}" width="{PANEL_L_W}" height="{BODY_H}" rx="12" fill="#FFFFFF" stroke="#E2E8F0" stroke-width="1"/>')
    parts.append(f'<text x="{PAD_X+18}" y="{p1_y+26}" font-size="15" font-weight="700" fill="#0F172A">1. 日別タイムライン（0:00〜24:00）</text>')

    tl_x = PAD_X + 104
    tl_w = PANEL_L_W - 220
    tl_top = p1_y + 52
    tl_bot = tl_top + n_days * ROW_H

    for hr in range(25):
        gx = tl_x + (hr / 24.0) * tl_w
        major = (hr % 3 == 0)
        parts.append(f'<line x1="{gx:.1f}" y1="{tl_top}" x2="{gx:.1f}" y2="{tl_bot}" stroke="{"#CBD5E1" if major else "#F1F5F9"}" stroke-width="{"1" if major else "0.7"}"/>')
        if hr % 3 == 0:
            anc = 'end' if hr == 24 else 'middle'
            parts.append(f'<text x="{gx:.1f}" y="{tl_top-7}" text-anchor="{anc}" font-size="10" fill="#64748B">{hr:02d}:00</text>')

    sum_x = tl_x + tl_w + 12
    parts.append(f'<text x="{sum_x}" y="{tl_top-7}" font-size="10" font-weight="700" fill="#334155">操作 / AI</text>')

    for i, ds in enumerate(sorted_ds):
        info = days[ds]
        d = info['date']
        wd = d.weekday()
        ry = tl_top + i * ROW_H
        if wd in (5, 6):
            parts.append(f'<rect x="{PAD_X+10}" y="{ry}" width="{PANEL_L_W-20}" height="{ROW_H}" fill="#EFF6FF" opacity="0.45"/>')
        parts.append(f'<line x1="{PAD_X+10}" y1="{ry+ROW_H}" x2="{PAD_X+PANEL_L_W-10}" y2="{ry+ROW_H}" stroke="#F1F5F9" stroke-width="0.7"/>')

        d_col = '#2563EB' if wd == 5 else ('#DC2626' if wd == 6 else '#1E293B')
        parts.append(f'<text x="{tl_x-8}" y="{ry+ROW_H*0.68:.1f}" text-anchor="end" font-size="11.2" font-weight="600" fill="{d_col}">{d.month:02d}/{d.day:02d}({WD_NAMES[wd]})</text>')

        bh = ROW_H - 8
        by = ry + 4
        # Draw recorded segments
        for h0, h1, cat in info['segments']:
            sx = tl_x + (h0 / 24.0) * tl_w
            sw = max(1.2, ((h1 - h0) / 24.0) * tl_w)
            parts.append(f'<rect x="{sx:.1f}" y="{by}" width="{sw:.1f}" height="{bh}" rx="1.5" fill="{CAT_COLORS[cat]}"/>')

        # Optional meeting overlay
        for h0, h1, _ in meetings_by_day.get(ds, []):
            sx = tl_x + (h0 / 24.0) * tl_w
            sw = max(2.0, ((h1 - h0) / 24.0) * tl_w)
            parts.append(f'<rect x="{sx:.1f}" y="{by-1}" width="{sw:.1f}" height="{bh+2}" rx="2" fill="#1D4ED8" fill-opacity="0.22" stroke="#1D4ED8" stroke-width="1"/>')

        mac_s = info['cat_s']['human_only'] + info['cat_s']['human_and_ai']
        ai_s = info['cat_s']['human_and_ai'] + info['cat_s']['away_ai']
        if mac_s + ai_s > 0:
            parts.append(f'<text x="{sum_x}" y="{ry+ROW_H*0.68:.1f}" font-size="10.2" fill="#334155">{fmt_hm(mac_s)} / <tspan fill="#EA580C" font-weight="700">{fmt_hm(ai_s)}</tspan></text>')
        else:
            parts.append(f'<text x="{sum_x}" y="{ry+ROW_H*0.68:.1f}" font-size="10" fill="#CBD5E1">—</text>')

    parts.append(f'<rect x="{tl_x}" y="{tl_top}" width="{tl_w}" height="{n_days*ROW_H}" fill="none" stroke="#CBD5E1" stroke-width="1"/>')

    # Right Panel: App Ranking + Agent Breakdown
    parts.append(f'<rect x="{PANEL_R_X}" y="{p1_y}" width="{PANEL_R_W}" height="{BODY_H}" rx="12" fill="#FFFFFF" stroke="#E2E8F0" stroke-width="1"/>')
    parts.append(f'<text x="{PANEL_R_X+18}" y="{p1_y+26}" font-size="15" font-weight="700" fill="#0F172A">2. アプリ別 操作時間ランキング</text>')

    top_apps = sorted(totals['app_active_s'].items(), key=lambda x: x[1], reverse=True)[:10]
    ay = p1_y + 52
    max_app_s = top_apps[0][1] if top_apps else 1.0
    bar_x = PANEL_R_X + 140
    bar_max = PANEL_R_W - 225
    for app, sec in top_apps:
        app_esc = html.escape(app[:18])
        parts.append(f'<text x="{bar_x-10}" y="{ay+14}" text-anchor="end" font-size="11.5" font-weight="600" fill="#1E293B">{app_esc}</text>')
        bw0 = max(2.0, (sec / max_app_s) * bar_max)
        parts.append(f'<rect x="{bar_x}" y="{ay+2}" width="{bw0:.1f}" height="16" rx="3" fill="#3B82F6" opacity="0.85"/>')
        parts.append(f'<text x="{bar_x + bw0 + 8:.1f}" y="{ay+14}" font-size="11" font-weight="700" fill="#334155">{fmt_hm(sec)}</text>')
        ay += 26

    if not top_apps:
        parts.append(f'<text x="{PANEL_R_X+20}" y="{ay+12}" font-size="12" fill="#94A3B8">操作データがありません</text>')
        ay += 30

    # Agent breakdown below apps
    ag_y = max(ay + 20, p1_y + 320)
    parts.append(f'<line x1="{PANEL_R_X+18}" y1="{ag_y-14}" x2="{PANEL_R_X+PANEL_R_W-18}" y2="{ag_y-14}" stroke="#E2E8F0" stroke-width="1"/>')
    parts.append(f'<text x="{PANEL_R_X+18}" y="{ag_y+8}" font-size="15" font-weight="700" fill="#0F172A">3. AIエージェント別 稼働時間</text>')

    top_agents = sorted(totals['agent_s'].items(), key=lambda x: x[1], reverse=True)
    gy = ag_y + 30
    max_ag_s = top_agents[0][1] if top_agents else 1.0
    for ag, sec in top_agents:
        parts.append(f'<text x="{bar_x-10}" y="{gy+14}" text-anchor="end" font-size="11.5" font-weight="700" fill="#1E293B">{html.escape(ag)}</text>')
        bw0 = max(2.0, (sec / max_ag_s) * bar_max)
        parts.append(f'<rect x="{bar_x}" y="{gy+2}" width="{bw0:.1f}" height="16" rx="3" fill="#F97316" opacity="0.88"/>')
        parts.append(f'<text x="{bar_x + bw0 + 8:.1f}" y="{gy+14}" font-size="11" font-weight="700" fill="#C2410C">{fmt_hm(sec)}</text>')
        gy += 26

    if not top_agents:
        parts.append(f'<text x="{PANEL_R_X+20}" y="{gy+12}" font-size="12" fill="#94A3B8">AI稼働データがありません</text>')

    parts.append(f'<text x="{PAD_X}" y="{H-14}" font-size="11" fill="#64748B">※ ~/.ccmon/activity/YYYY-MM.jsonl から自動集計。白抜き部分は Mac スリープまたは ccmon 未起動の時間帯です。</text>')
    parts.append('</svg>')

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text('\n'.join(parts), encoding='utf-8')


def export_image(result: dict, out_dir: Path, stem: str, meetings_by_day: Optional[dict] = None) -> Path:
    """SVG を生成し、rsvg-convert が利用可能なら高解像度 PNG に変換して返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f'{stem}.svg'
    render_dashboard_svg(result, svg_path, meetings_by_day=meetings_by_day)
    if shutil.which('rsvg-convert'):
        png_path = out_dir / f'{stem}.png'
        subprocess.run(['rsvg-convert', '-z', '1.5', '-o', str(png_path), str(svg_path)], check=True)
        return png_path
    return svg_path


def resolve_date_range(args) -> tuple[date, date, str]:
    today = date.today()
    if args.date:
        d = date.fromisoformat(args.date)
        return d, d, f'activity_{d.isoformat()}'
    if args.days:
        n = max(1, int(args.days))
        s = today - timedelta(days=n - 1)
        return s, today, f'activity_last_{n}d_{today.isoformat()}'
    ym = args.month or today.strftime('%Y-%m')
    y, m = map(int, ym.split('-'))
    s = date(y, m, 1)
    if m == 12:
        last_of_month = date(y, 12, 31)
    else:
        last_of_month = date(y, m + 1, 1) - timedelta(days=1)
    e = min(today, last_of_month) if ym == today.strftime('%Y-%m') else last_of_month
    return s, e, f'activity_{ym}'


def main(argv=None):
    parser = argparse.ArgumentParser(description='ccmon 活動ログから時間配分を集計・可視化します。')
    parser.add_argument('--month', help='対象月 (YYYY-MM、既定: 今月)')
    parser.add_argument('--days', type=int, help='直近N日間を集計')
    parser.add_argument('--date', help='指定日のみ集計 (YYYY-MM-DD)')
    parser.add_argument('--dir', dest='log_dir', default=str(DEFAULT_LOG_DIR), help='活動ログディレクトリ')
    parser.add_argument('--out-dir', default=str(DEFAULT_OUT_DIR), help='画像出力先ディレクトリ')
    parser.add_argument('--meetings', help='任意: 会議CSVファイルパス')
    parser.add_argument('--no-image', action='store_true', help='画像出力を行わずテキストのみ表示')
    args = parser.parse_args(argv)

    start_d, end_d, stem = resolve_date_range(args)
    records = load_records(Path(args.log_dir), start_d, end_d)
    result = analyze_records(records, start_d, end_d)
    print_text_report(result)

    if not args.no_image and records:
        meetings = load_meetings_csv(Path(args.meetings)) if args.meetings else None
        out_path = export_image(result, Path(args.out_dir), stem, meetings_by_day=meetings)
        print(f'\nグラフを出力しました: {out_path}')


if __name__ == '__main__':
    main()
