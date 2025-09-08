#import "Logger.h"

@implementation Logger

static NSFileHandle *_fh;
static BOOL _debug = NO;

+ (void)setupWithArgc:(int)argc argv:(const char *[])argv {
    // デバッグ有効化: 環境変数 CCMON_DEBUG=1 または -v/--debug フラグ
    NSDictionary *env = [[NSProcessInfo processInfo] environment];
    if ([env[@"CCMON_DEBUG"] isEqualToString:@"1"]) {
        _debug = YES;
    }
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--debug") == 0) {
            _debug = YES;
        }
    }

    // ログファイル: ~/Library/Logs/ccMonMac/ccMonMac.log（上書きせず追記）
    NSString *custom = env[@"CCMON_LOG"];
    NSString *logDirPath = custom.length ? [custom stringByDeletingLastPathComponent]
                                         : [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/ccMonMac"];
    NSString *logFilePath = custom.length ? custom
                                         : [logDirPath stringByAppendingPathComponent:@"ccMonMac.log"];
    NSError *err = nil;
    [[NSFileManager defaultManager] createDirectoryAtPath:logDirPath withIntermediateDirectories:YES attributes:nil error:&err];
    if (err) {
        fprintf(stderr, "[ccMonMac] failed to create log dir: %s\n", err.localizedDescription.UTF8String);
    }
    if (![[NSFileManager defaultManager] fileExistsAtPath:logFilePath]) {
        [@"" writeToFile:logFilePath atomically:YES encoding:NSUTF8StringEncoding error:nil];
    }
    _fh = [NSFileHandle fileHandleForWritingAtPath:logFilePath];
    [_fh seekToEndOfFile];

    // 標準出力/標準エラーは強制フラッシュ
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);

    [self log:CCLogLevelInfo fmt:@"Logger initialized. debug=%@ file=%@", _debug ? @"YES" : @"NO", logFilePath];
}

+ (BOOL)isDebug { return _debug; }

+ (void)log:(CCLogLevel)level fmt:(NSString *)fmt, ... {
    if (!_fh) return;
    va_list args;
    va_start(args, fmt);
    NSString *msg = [[NSString alloc] initWithFormat:fmt arguments:args];
    va_end(args);

    NSDateFormatter *df = [NSDateFormatter new];
    df.locale = [NSLocale localeWithLocaleIdentifier:@"en_US_POSIX"];
    df.dateFormat = @"yyyy-MM-dd HH:mm:ss.SSS";
    NSString *ts = [df stringFromDate:[NSDate date]];
    NSString *lv = (level == CCLogLevelError) ? @"ERROR" : (level == CCLogLevelDebug ? @"DEBUG" : @"INFO");
    NSString *line = [NSString stringWithFormat:@"[%@] %@ %@\n", ts, lv, msg];

    // stderr にも出す
    fprintf(stderr, "%s", line.UTF8String);

    @synchronized (self) {
        @try {
            [_fh writeData:[line dataUsingEncoding:NSUTF8StringEncoding]];
            [_fh synchronizeFile];
        } @catch (__unused NSException *e) {
        }
    }
}

@end

