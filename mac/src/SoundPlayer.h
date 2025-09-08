#import <Foundation/Foundation.h>

typedef NS_ENUM(NSInteger, SPVolumeLevel) {
    SPVolumeLevelSmall = 0,
    SPVolumeLevelMedium = 1,
    SPVolumeLevelLarge = 2
};

@interface SoundPlayer : NSObject
- (void)playBeeps;           // 10秒間、ランダムな間隔とピッチで再生
- (void)quickSystemBeep;     // 短い確認音
- (void)playSampleBeep;      // 設定変更時のプレビュー用（自前合成）
- (void)stop;                // 再生停止
// 音量: 小/中/大（既定: 中）
- (void)setVolumeLevel:(SPVolumeLevel)level;
- (SPVolumeLevel)volumeLevel;
- (BOOL)isPlaying;           // 再生中フラグ（点滅用）
@end
