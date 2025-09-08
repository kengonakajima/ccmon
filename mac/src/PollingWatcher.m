#import "PollingWatcher.h"

@interface PollingWatcher ()
@property (nonatomic, copy) NSString *rootPath;
@property (nonatomic, copy) NSSet<NSString *> *exts;
@property (nonatomic) NSTimeInterval interval;
@property (nonatomic, copy) PWEventHandler handler;
@property (nonatomic) dispatch_source_t timer;
@property (nonatomic, strong) NSDictionary<NSString *, NSDictionary *> *snapshot; // path -> { mtime, size }
@property (nonatomic) dispatch_queue_t queue;
@end

@implementation PollingWatcher

- (instancetype)initWithPath:(NSString *)path
                     interval:(NSTimeInterval)interval
                   extensions:(NSArray<NSString *> *)extensions
                      handler:(PWEventHandler)handler {
    if (self = [super init]) {
        _rootPath = [path copy];
        NSMutableSet *s = [NSMutableSet set];
        for (NSString *e in extensions) { [s addObject:e.lowercaseString]; }
        _exts = [s copy];
        _interval = interval > 0 ? interval : 1.0;
        _handler = [handler copy];
        _queue = dispatch_queue_create("ccmon.polling", DISPATCH_QUEUE_SERIAL);
        _snapshot = @{};
    }
    return self;
}

- (void)start {
    if (self.timer) return;
    __weak typeof(self) weakSelf = self;
    self.timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, self.queue);
    dispatch_source_set_timer(self.timer, DISPATCH_TIME_NOW, (uint64_t)(self.interval * NSEC_PER_SEC), (uint64_t)(0.1 * NSEC_PER_SEC));
    dispatch_source_set_event_handler(self.timer, ^{ [weakSelf scanOnce]; });
    dispatch_resume(self.timer);
}

- (void)stop {
    if (!self.timer) return;
    dispatch_source_cancel(self.timer);
    self.timer = nil;
}

- (void)dealloc { [self stop]; }

- (void)scanOnce {
    NSString *root = self.rootPath;
    NSFileManager *fm = [NSFileManager defaultManager];
    NSURL *rootURL = [NSURL fileURLWithPath:root isDirectory:YES];
    NSArray *keys = @[NSURLIsDirectoryKey, NSURLContentModificationDateKey, NSURLFileSizeKey];
    NSDirectoryEnumerator *en = [fm enumeratorAtURL:rootURL
                        includingPropertiesForKeys:keys
                                           options:NSDirectoryEnumerationSkipsHiddenFiles
                                      errorHandler:^BOOL(NSURL *url, NSError *error) {
        return YES;
    }];
    NSMutableDictionary *newSnap = [NSMutableDictionary dictionaryWithCapacity:256];
    NSMutableArray<NSString *> *changed = [NSMutableArray array];

    for (NSURL *url in en) {
        NSNumber *isDir = nil; [url getResourceValue:&isDir forKey:NSURLIsDirectoryKey error:nil];
        if (isDir.boolValue) continue;
        NSString *ext = url.pathExtension.lowercaseString ?: @"";
        if (self.exts.count > 0 && ![self.exts containsObject:ext]) continue;
        NSDate *mod = nil; [url getResourceValue:&mod forKey:NSURLContentModificationDateKey error:nil];
        NSNumber *size = nil; [url getResourceValue:&size forKey:NSURLFileSizeKey error:nil];
        NSTimeInterval mtime = mod ? mod.timeIntervalSince1970 : 0;
        NSDictionary *info = @{ @"mtime": @(mtime), @"size": (size ?: @(0)) };
        NSString *path = url.path;
        NSDictionary *prev = self.snapshot[path];
        if (!prev || [prev[@"mtime"] doubleValue] != mtime || [prev[@"size"] longLongValue] != [size longLongValue]) {
            [changed addObject:path];
        }
        newSnap[path] = info;
    }

    self.snapshot = newSnap;
    if (changed.count > 0 && self.handler) {
        for (NSString *p in changed) {
            dispatch_async(dispatch_get_main_queue(), ^{ self.handler(p); });
        }
    }
}

@end

