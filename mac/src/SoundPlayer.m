#import "SoundPlayer.h"
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <Cocoa/Cocoa.h>
#import "Logger.h"

@interface SoundPlayer ()
@property (nonatomic, strong) AVAudioEngine *engine;
@property (nonatomic, strong) AVAudioPlayerNode *player;
@property (nonatomic, strong) AVAudioFormat *format;
@property (atomic, assign) BOOL playing;
@property (atomic, assign) BOOL stopRequested;
@property (atomic, assign) float masterGain;      // 0.0 - 1.0
@property (atomic, assign) SPVolumeLevel level;    // 保存用
@end

@implementation SoundPlayer

- (instancetype)init {
    if (self = [super init]) {
        [self setupEngine];
        [self registerDeviceChangeListener];
        // 既定は中
        [self setVolumeLevel:SPVolumeLevelMedium];
    }
    return self;
}

- (void)setupEngine {
    [Logger log:CCLogLevelInfo fmt:@"Audio engine setup start"];    
    self.engine = [AVAudioEngine new];
    self.player = [AVAudioPlayerNode new];
    self.format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:44100.0 channels:1];
    [self.engine attachNode:self.player];
    [self.engine connect:self.player to:self.engine.mainMixerNode format:self.format];
    NSError *err = nil;
    [self.engine startAndReturnError:&err];
    if (err) {
        [Logger log:CCLogLevelError fmt:@"Audio engine start error: %@", err];
    }
}

- (void)restartEngine {
    @synchronized (self) {
        if (self.engine.isRunning) {
            [self.engine pause];
            [self.engine stop];
        }
        self.engine = nil;
        self.player = nil;
        [self setupEngine];
    }
}

- (void)registerDeviceChangeListener {
    AudioObjectPropertyAddress addr = {
        kAudioHardwarePropertyDefaultOutputDevice,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    AudioObjectAddPropertyListenerBlock(kAudioObjectSystemObject, &addr, dispatch_get_main_queue(), ^(UInt32 inNumberAddresses, const AudioObjectPropertyAddress *inAddresses) {
        [Logger log:CCLogLevelInfo fmt:@"Default output device changed"];    
        [self restartEngine];
    });
}

- (AVAudioPCMBuffer *)makeBeepBufferWithFrequency:(float)freq duration:(float)duration {
    AVAudioFrameCount frames = (AVAudioFrameCount)(self.format.sampleRate * duration);
    AVAudioPCMBuffer *buffer = [[AVAudioPCMBuffer alloc] initWithPCMFormat:self.format frameCapacity:frames];
    buffer.frameLength = frames;
    float *samples = buffer.floatChannelData[0];
    float sampleRate = (float)self.format.sampleRate;
    float amp = self.masterGain;
    // ハン窓（Hann）で 10ms のフェードイン/アウトを付与しクリック除去
    UInt32 ramp = (UInt32)(sampleRate * 0.01f); // 10ms
    if (ramp * 2 > frames) ramp = (UInt32)(frames / 4); // 異常短小を回避
    for (AVAudioFrameCount i = 0; i < frames; i++) {
        float t = (float)i / sampleRate;
        float s = sinf(2.0f * (float)M_PI * freq * t);
        // フェードイン/アウト（Hann）
        float env = 1.0f;
        if (i < ramp) {
            env *= 0.5f * (1.0f - cosf((float)M_PI * (float)i / (float)ramp));
        }
        if (i >= frames - ramp) {
            float k = (float)(i - (frames - ramp));
            env *= 0.5f * (1.0f + cosf((float)M_PI * k / (float)ramp));
        }
        samples[i] = amp * s * env;
    }
    return buffer;
}

- (void)playBeeps {
    if (self.playing) { [Logger log:CCLogLevelDebug fmt:@"playBeeps: already playing"]; return; }
    self.playing = YES;
    self.stopRequested = NO;

    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        typeof(self) strongSelf = weakSelf;
        if (!strongSelf) return;
        NSTimeInterval start = [NSDate date].timeIntervalSince1970;
        while (!strongSelf.stopRequested && ([NSDate date].timeIntervalSince1970 - start) < 10.0) {
            float freq = 400.0f + arc4random_uniform(1200); // 400-1600Hz
            AVAudioPCMBuffer *buf = [strongSelf makeBeepBufferWithFrequency:freq duration:0.05f];

            @synchronized (strongSelf) {
                NSError *err = nil;
                if (!strongSelf.engine.isRunning) {
                    [strongSelf.engine startAndReturnError:&err];
                    if (err) [Logger log:CCLogLevelError fmt:@"Engine restart error: %@", err];
                }
                if (!strongSelf.player.isPlaying) {
                    [strongSelf.player play];
                }
                [strongSelf.player scheduleBuffer:buf completionHandler:nil];
            }

            // 無音区間: 0.15 - 0.8 秒（やや短めにして隙間を縮小）
            float silence = 0.15f + ((float)arc4random() / UINT32_MAX) * 0.65f;
            [NSThread sleepForTimeInterval:silence];
        }

        [Logger log:CCLogLevelDebug fmt:@"playBeeps: finished"];    
        strongSelf.playing = NO;
    });
}

- (void)quickSystemBeep {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        // NSSound でシステム音を試す
        NSString *pop = @"/System/Library/Sounds/Pop.aiff";
        if ([[NSFileManager defaultManager] fileExistsAtPath:pop]) {
            NSSound *s = [[NSSound alloc] initWithContentsOfFile:pop byReference:YES];
            [s play];
        } else {
            NSBeep();
        }
    });
}

- (void)playSampleBeep {
    // 自前の合成で 0.15s / 800Hz を1回だけ再生（音量は masterGain に従う）
    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        typeof(self) strongSelf = weakSelf; if (!strongSelf) return;
        AVAudioPCMBuffer *buf = [strongSelf makeBeepBufferWithFrequency:800.0f duration:0.15f];
        @synchronized (strongSelf) {
            NSError *err = nil;
            if (!strongSelf.engine.isRunning) {
                [strongSelf.engine startAndReturnError:&err];
                if (err) [Logger log:CCLogLevelError fmt:@"Engine restart error(sample): %@", err];
            }
            if (!strongSelf.player.isPlaying) {
                [strongSelf.player play];
            }
            [strongSelf.player scheduleBuffer:buf completionHandler:nil];
        }
    });
}

- (void)stop {
    self.stopRequested = YES;
    [Logger log:CCLogLevelDebug fmt:@"SoundPlayer stop"];    
    @synchronized (self) {
        if (self.player.isPlaying) {
            [self.player stop];
            if ([self.player respondsToSelector:@selector(reset)]) {
                [self.player reset]; // 予約済みバッファをクリア
            }
        }
        if (self.engine.isRunning) {
            [self.engine pause];
            [self.engine stop];
        }
    }
    self.playing = NO; // 点滅表示を即時オフ
}

// MARK: - Volume control

- (void)setVolumeLevel:(SPVolumeLevel)level {
    _level = level;
    switch (level) {
        case SPVolumeLevelSmall:  self.masterGain = 0.08f; break;
        case SPVolumeLevelLarge:  self.masterGain = 0.30f; break;
        case SPVolumeLevelMedium:
        default:                  self.masterGain = 0.18f; break;
    }
    [Logger log:CCLogLevelInfo fmt:@"volume level set -> %ld (gain=%.2f)", (long)level, self.masterGain];
}

- (SPVolumeLevel)volumeLevel {
    return _level;
}

- (BOOL)isPlaying {
    return self.playing;
}

@end
