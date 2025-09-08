#import "AppDelegate.h"
#import <Cocoa/Cocoa.h>
#import "SoundPlayer.h"
#import "FileWatcher.h"
#import "PollingWatcher.h"
#import "Logger.h"

@interface AppDelegate ()
@property (strong) NSStatusItem *statusItem;
@property (assign) BOOL soundEnabled;
@property (strong) NSMenu *statusMenu;
@property (strong) NSMenuItem *volLargeItem;
@property (strong) NSMenuItem *volMediumItem;
@property (strong) NSMenuItem *volSmallItem;
@property (strong) NSTimer *blinkTimer;
@property (assign) BOOL blinkOn;
@property (assign) BOOL lastPlaying;
@property (strong) NSTimer *claudeDeferred;
@property (strong) NSTimer *codexDeferred;
@property (strong) NSTimer *cursorDeferred;
@property (assign) BOOL useTextIcon;
@property (strong) NSImage *iconA; // 点滅A
@property (strong) NSImage *iconB; // 点滅B/待機
@property (strong) SoundPlayer *soundPlayer;
@property (strong) FileWatcher *claudeWatcher;
@property (strong) FileWatcher *codexWatcher; // 未使用（Claude用に残置）
@property (strong) PollingWatcher *codexPoller;
@property (strong) FileWatcher *cursorWatcher;
@property (strong) NSDate *lastClaudePlayed;
@property (strong) NSDate *lastCodexPlayed;
@property (strong) NSDate *lastCursorPlayed;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [Logger log:CCLogLevelInfo fmt:@"applicationDidFinishLaunching"];    
    self.soundEnabled = YES;
    self.soundPlayer = [[SoundPlayer alloc] init];
    // 既定音量（ユーザーデフォルト）
    NSInteger saved = [[NSUserDefaults standardUserDefaults] integerForKey:@"com.ccmon.mac.volume"];
    if (saved < 0 || saved > 2) saved = 1; // 中
    [self.soundPlayer setVolumeLevel:(SPVolumeLevel)saved];
    [self setupStatusItem];
    [self setupWatchers];
    // 起動直後の自動メニュー表示は行わない
    // 再生中の可視化（cc / cc* の点滅）
    __weak typeof(self) weakSelfBlink = self;
    self.blinkTimer = [NSTimer scheduledTimerWithTimeInterval:0.4 repeats:YES block:^(NSTimer * _Nonnull t) {
        typeof(self) selfRef = weakSelfBlink; if (!selfRef) return;
        BOOL playing = [selfRef.soundPlayer isPlaying];
        if (selfRef.useTextIcon) {
            if (playing) {
                selfRef.blinkOn = !selfRef.blinkOn;
                selfRef.statusItem.button.title = selfRef.blinkOn ? @"cc*" : @"cc";
            } else if (selfRef.lastPlaying) {
                selfRef.statusItem.button.title = @"cc";
                selfRef.blinkOn = NO;
            }
        } else {
            if (playing) {
                selfRef.blinkOn = !selfRef.blinkOn;
                selfRef.statusItem.button.image = selfRef.blinkOn ? selfRef.iconA : selfRef.iconB;
            } else if (selfRef.lastPlaying) {
                selfRef.statusItem.button.image = selfRef.iconB;
                selfRef.blinkOn = NO;
            }
        }
        selfRef.lastPlaying = playing;
    }];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [Logger log:CCLogLevelInfo fmt:@"applicationWillTerminate"];    
    [self.claudeWatcher stop];
    [self.codexWatcher stop];
    [self.cursorWatcher stop];
    [self.soundPlayer stop];
}

-(void)setupStatusItem {
    [Logger log:CCLogLevelInfo fmt:@"setupStatusItem"];    
    // メニュー作成・保持
    NSMenu *menu = [[NSMenu alloc] initWithTitle:@"ccMonMac"];
    self.statusMenu = menu;

    NSMenuItem *toggleItem = [[NSMenuItem alloc] initWithTitle:@"サウンド: On"
                                                        action:@selector(toggleSound:)
                                                 keyEquivalent:@""];
    [toggleItem setTarget:self];
    [menu addItem:toggleItem];

    // 音量サブメニュー
    NSMenu *volMenu = [[NSMenu alloc] initWithTitle:@"音量"];
    self.volLargeItem = [[NSMenuItem alloc] initWithTitle:@"大" action:@selector(selectVolumeLarge:) keyEquivalent:@""];
    self.volMediumItem = [[NSMenuItem alloc] initWithTitle:@"中" action:@selector(selectVolumeMedium:) keyEquivalent:@""];
    self.volSmallItem = [[NSMenuItem alloc] initWithTitle:@"小" action:@selector(selectVolumeSmall:) keyEquivalent:@""];
    [self.volLargeItem setTarget:self];
    [self.volMediumItem setTarget:self];
    [self.volSmallItem setTarget:self];
    [volMenu addItem:self.volLargeItem];
    [volMenu addItem:self.volMediumItem];
    [volMenu addItem:self.volSmallItem];
    NSMenuItem *volRoot = [[NSMenuItem alloc] initWithTitle:@"音量" action:nil keyEquivalent:@""];
    volRoot.submenu = volMenu;
    [menu addItem:volRoot];

    [menu addItem:[NSMenuItem separatorItem]];

    NSMenuItem *quitItem = [[NSMenuItem alloc] initWithTitle:@"終了"
                                                      action:@selector(quitApp:)
                                               keyEquivalent:@"q"];
    [quitItem setTarget:self];
    [menu addItem:quitItem];

    [self createStatusItemAndAttachMenu];
    [Logger log:CCLogLevelInfo fmt:@"status item created, menu prepared"];    
    [self updateVolumeChecks];
}

- (void)handleStatusItemClick:(id)sender {
    [Logger log:CCLogLevelDebug fmt:@"status item clicked"];    
    [self.statusItem popUpStatusItemMenu:self.statusItem.menu];
}

- (void)createStatusItemAndAttachMenu {
    if (self.statusItem) {
        [[NSStatusBar systemStatusBar] removeStatusItem:self.statusItem];
        self.statusItem = nil;
    }
    // テキストアイコン強制（環境変数）。既定はアイコン表示
    self.useTextIcon = [[[NSProcessInfo processInfo] environment][@"CCMON_TEXT_ICON"] isEqualToString:@"1"];
    CGFloat fixedLen = self.useTextIcon ? 28.0 : 24.0;
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:fixedLen];
    self.statusItem.autosaveName = @"com.ccmon.mac.status"; // 位置保存を有効化
    NSStatusBarButton *button = self.statusItem.button;
    if (self.useTextIcon) {
        button.title = @"cc"; // テキストのみ
        button.image = nil;
        button.imagePosition = NSNoImage;
    } else {
        // 同サイズのテンプレート画像を2つ持ち、再生中は点滅切替
        self.iconA = [self buildTemplateIconVariant:YES];
        self.iconB = [self buildTemplateIconVariant:NO];
        button.title = @"";
        button.image = self.iconA;
        button.imagePosition = NSImageOnly;
    }
    button.toolTip = @"ccMonMac";
    button.target = self;
    button.action = @selector(handleStatusItemClick:);
    self.statusItem.menu = self.statusMenu;
    [Logger log:CCLogLevelInfo fmt:@"status icon set: %@ & attached menu",
     (self.useTextIcon ? @"TEXT" : @"IMAGE")];
}

// 音量メニューのチェック更新と選択処理
- (void)updateVolumeChecks {
    self.volLargeItem.state = (self.soundPlayer.volumeLevel == SPVolumeLevelLarge) ? NSControlStateValueOn : NSControlStateValueOff;
    self.volMediumItem.state = (self.soundPlayer.volumeLevel == SPVolumeLevelMedium) ? NSControlStateValueOn : NSControlStateValueOff;
    self.volSmallItem.state = (self.soundPlayer.volumeLevel == SPVolumeLevelSmall) ? NSControlStateValueOn : NSControlStateValueOff;
}

- (void)applyVolume:(SPVolumeLevel)level {
    [self.soundPlayer setVolumeLevel:level];
    [[NSUserDefaults standardUserDefaults] setInteger:level forKey:@"com.ccmon.mac.volume"];
    [[NSUserDefaults standardUserDefaults] synchronize];
    [self updateVolumeChecks];
    // プレビュー再生（On/Offに関わらず鳴らす）
    [self.soundPlayer playSampleBeep];
}

- (void)selectVolumeLarge:(id)sender { [self applyVolume:SPVolumeLevelLarge]; }
- (void)selectVolumeMedium:(id)sender { [self applyVolume:SPVolumeLevelMedium]; }
- (void)selectVolumeSmall:(id)sender { [self applyVolume:SPVolumeLevelSmall]; }

- (void)toggleSound:(id)sender {
    self.soundEnabled = !self.soundEnabled;
    NSMenuItem *item = (NSMenuItem *)sender;
    item.title = self.soundEnabled ? @"サウンド: On" : @"サウンド: Off";
    if (self.soundEnabled) {
        [self.soundPlayer quickSystemBeep];
    } else {
        // 即時停止: 再生中のバッファ/スレッドを止め、デファード鳴動もキャンセル
        [self.soundPlayer stop];
        [self cancelDeferredForLabel:@"Claude"];
        [self cancelDeferredForLabel:@"Codex"];
        [self cancelDeferredForLabel:@"Cursor"];
    }
    [Logger log:CCLogLevelInfo fmt:@"toggleSound -> %@", self.soundEnabled ? @"On" : @"Off"];    
}

- (void)quitApp:(id)sender {
    [NSApp terminate:nil];
}

- (void)setupWatchers {
    [Logger log:CCLogLevelInfo fmt:@"setupWatchers"];    
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *home = NSHomeDirectory();
    NSString *claudePath = [home stringByAppendingPathComponent:@".claude/projects"];
    NSString *codexPath  = [home stringByAppendingPathComponent:@".codex/sessions"];
    NSString *cursorPath = [home stringByAppendingPathComponent:@".cursor/chats"];

    self.lastClaudePlayed = [NSDate dateWithTimeIntervalSinceNow:-11];
    self.lastCodexPlayed  = [NSDate dateWithTimeIntervalSinceNow:-11];
    self.lastCursorPlayed = [NSDate dateWithTimeIntervalSinceNow:-11];

    BOOL isDir = NO;
    if ([fm fileExistsAtPath:claudePath isDirectory:&isDir] && isDir) {
        __weak typeof(self) weakSelf = self;
        self.claudeWatcher = [[FileWatcher alloc] initWithPaths:@[claudePath]
                                                        handler:^(NSString *path, FSEventStreamEventFlags flags) {
            if (![weakSelf shouldHandleFlags:flags]) return;
            if (![path.pathExtension.lowercaseString isEqualToString:@"jsonl"]) return;
            [weakSelf throttleAndPlay:&_lastClaudePlayed label:@"Claude" filePath:path];
        }];
        [self.claudeWatcher start];
        [Logger log:CCLogLevelInfo fmt:@"Claude watch started: %@", claudePath];
    } else {
        [Logger log:CCLogLevelInfo fmt:@"Claude path not found: %@", claudePath];
    }

    isDir = NO;
    if ([fm fileExistsAtPath:codexPath isDirectory:&isDir] && isDir) {
        __weak typeof(self) weakSelf = self;
        self.codexPoller = [[PollingWatcher alloc] initWithPath:codexPath
                                                       interval:1.0
                                                     extensions:@[@"json", @"jsonl"]
                                                        handler:^(NSString *changedPath) {
            [weakSelf throttleAndPlay:&_lastCodexPlayed label:@"Codex" filePath:changedPath];
        }];
        [self.codexPoller start];
        [Logger log:CCLogLevelInfo fmt:@"Codex poller started (1s): %@", codexPath];
    } else {
        [Logger log:CCLogLevelInfo fmt:@"Codex path not found: %@", codexPath];
    }

    isDir = NO;
    if ([fm fileExistsAtPath:cursorPath isDirectory:&isDir] && isDir) {
        __weak typeof(self) weakSelf = self;
        self.cursorWatcher = [[FileWatcher alloc] initWithPaths:@[cursorPath]
                                                       handler:^(NSString *path, FSEventStreamEventFlags flags) {
            if (![weakSelf shouldHandleFlags:flags]) return;
            [weakSelf throttleAndPlay:&_lastCursorPlayed label:@"Cursor" filePath:path];
        }];
        [self.cursorWatcher start];
        [Logger log:CCLogLevelInfo fmt:@"Cursor watch started: %@", cursorPath];
    } else {
        [Logger log:CCLogLevelInfo fmt:@"Cursor path not found: %@", cursorPath];
    }
}

- (BOOL)shouldHandleFlags:(FSEventStreamEventFlags)flags {
    // 生成・変更イベントのみ処理
    BOOL created = (flags & kFSEventStreamEventFlagItemCreated) != 0;
    BOOL modified = (flags & kFSEventStreamEventFlagItemModified) != 0;
    BOOL renamed = (flags & kFSEventStreamEventFlagItemRenamed) != 0;
    if ([Logger isDebug]) {
        [Logger log:CCLogLevelDebug fmt:@"FSEvent flags: 0x%llx created=%d modified=%d renamed=%d", (unsigned long long)flags, created, modified, renamed];
    }
    return created || modified || renamed;
}

- (void)throttleAndPlay:(NSDate * __strong *)lastPlayed label:(NSString *)label filePath:(NSString *)path {
    NSDate *now = [NSDate date];
    NSTimeInterval elapsed = [now timeIntervalSinceDate:*lastPlayed];
    if (elapsed >= 10.0) {
        [Logger log:CCLogLevelInfo fmt:@"event %@: %@", label, path.lastPathComponent];
        *lastPlayed = now;
        [self cancelDeferredForLabel:label];
        if (self.soundEnabled) {
            [self.soundPlayer playBeeps];
        }
        return;
    }
    // 残り時間が短い場合は、許可時刻に自動で鳴らすデファードをセット
    NSTimeInterval remain = 10.0 - elapsed;
    if (remain <= 3.0) {
        if (![self hasDeferredForLabel:label]) {
            [Logger log:CCLogLevelDebug fmt:@"defer %@ for %.2fs (pending event: %@)", label, remain, path.lastPathComponent];
            __weak typeof(self) weakSelf = self;
            NSTimer *timer = [NSTimer scheduledTimerWithTimeInterval:(remain + 0.05)
                                                              repeats:NO
                                                                block:^(NSTimer * _Nonnull t) {
                typeof(self) strongSelf = weakSelf; if (!strongSelf) return;
                NSDate *now2 = [NSDate date];
                if ([now2 timeIntervalSinceDate:*lastPlayed] >= 10.0) {
                    *lastPlayed = now2;
                    if (strongSelf.soundEnabled) {
                        [Logger log:CCLogLevelInfo fmt:@"deferred beep fired: %@", label];
                        [strongSelf.soundPlayer playBeeps];
                    }
                }
                [strongSelf cancelDeferredForLabel:label];
            }];
            [self setDeferredTimer:timer forLabel:label];
        }
    }
}

- (BOOL)hasDeferredForLabel:(NSString *)label {
    if ([label isEqualToString:@"Claude"]) return self.claudeDeferred != nil;
    if ([label isEqualToString:@"Codex"])  return self.codexDeferred  != nil;
    if ([label isEqualToString:@"Cursor"]) return self.cursorDeferred != nil;
    return NO;
}

- (void)setDeferredTimer:(NSTimer *)timer forLabel:(NSString *)label {
    [self cancelDeferredForLabel:label];
    if ([label isEqualToString:@"Claude"]) self.claudeDeferred = timer;
    else if ([label isEqualToString:@"Codex"]) self.codexDeferred = timer;
    else if ([label isEqualToString:@"Cursor"]) self.cursorDeferred = timer;
}

- (void)cancelDeferredForLabel:(NSString *)label {
    NSTimer *timer = nil;
    if ([label isEqualToString:@"Claude"]) timer = self.claudeDeferred;
    else if ([label isEqualToString:@"Codex"]) timer = self.codexDeferred;
    else if ([label isEqualToString:@"Cursor"]) timer = self.cursorDeferred;
    if (timer) { [timer invalidate]; }
    if ([label isEqualToString:@"Claude"]) self.claudeDeferred = nil;
    else if ([label isEqualToString:@"Codex"]) self.codexDeferred = nil;
    else if ([label isEqualToString:@"Cursor"]) self.cursorDeferred = nil;
}

 - (NSImage *)buildTemplateIcon {
    CGFloat size = 18.0;
    NSImage *img = [[NSImage alloc] initWithSize:NSMakeSize(size, size)];
    [img lockFocus];
    [[NSColor blackColor] set];
    // 太めのリングを描いて、右側を削って「C」に見せる（アルファ重視のテンプレート）
    NSPoint center = NSMakePoint(size/2.0, size/2.0);
    CGFloat outerR = 7.5, innerR = 4.5;
    NSBezierPath *outer = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(center.x-outerR, center.y-outerR, outerR*2, outerR*2)];
    [outer fill];
    [[NSColor clearColor] set];
    NSRect cutRect = NSMakeRect(center.x, center.y-outerR, outerR, outerR*2);
    NSBezierPath *cut = [NSBezierPath bezierPathWithRect:cutRect];
    [cut setClip]; // 右半分を切り落とす
    [[NSColor blackColor] set];
    NSBezierPath *inner = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(center.x-innerR, center.y-innerR, innerR*2, innerR*2)];
    [[NSColor whiteColor] set]; // 中抜き
    [inner fill];
    // 小さな点
    [[NSColor blackColor] set];
    NSBezierPath *dot = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(center.x-1.0, center.y-1.0, 2.0, 2.0)];
    [dot fill];
    [img unlockFocus];
    img.template = YES;
    return img;
}

- (NSImage *)buildTemplateIconVariant:(BOOL)variantA {
    CGFloat size = 18.0;
    NSImage *img = [[NSImage alloc] initWithSize:NSMakeSize(size, size)];
    [img lockFocus];
    [[NSColor blackColor] set];
    NSPoint c = NSMakePoint(size/2.0, size/2.0);
    CGFloat r = 7.0;
    NSBezierPath *arc = [NSBezierPath bezierPath];
    [arc appendBezierPathWithArcWithCenter:c radius:r startAngle:40 endAngle:320 clockwise:NO];
    arc.lineWidth = variantA ? 2.4 : 1.6; // 位相違いで太さを少し変える
    [arc stroke];
    if (variantA) {
        NSBezierPath *dot = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(c.x+2.5, c.y-1.0, 2.2, 2.2)];
        [dot fill];
    }
    [img unlockFocus];
    img.template = YES;
    return img;
}

@end
