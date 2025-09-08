#import "FileWatcher.h"

@interface FileWatcher ()
@property (nonatomic) FSEventStreamRef stream;
@property (nonatomic, copy) FWEventHandler handler;
@property (nonatomic, strong) NSArray<NSString *> *paths;
@property (nonatomic) dispatch_queue_t queue;
@end

static void FWCallback(ConstFSEventStreamRef streamRef,
                       void *clientCallBackInfo,
                       size_t numEvents,
                       void *eventPaths,
                       const FSEventStreamEventFlags eventFlags[],
                       const FSEventStreamEventId eventIds[]) {
    FileWatcher *watcher = (__bridge FileWatcher *)clientCallBackInfo;
    if (!watcher || !watcher.handler) return;
    NSArray *paths = (__bridge NSArray *)eventPaths; // kFSEventStreamCreateFlagUseCFTypes を使う
    for (size_t i = 0; i < numEvents; i++) {
        NSString *path = paths[i];
        FSEventStreamEventFlags flags = eventFlags[i];
        // ディレクトリ通過イベントの際は path が対象ディレクトリの可能性があるため、そのまま渡す
        watcher.handler(path, flags);
    }
}

@implementation FileWatcher

- (instancetype)initWithPaths:(NSArray<NSString *> *)paths handler:(FWEventHandler)handler {
    if (self = [super init]) {
        _paths = [paths copy];
        _handler = [handler copy];
        _queue = dispatch_queue_create("ccmon.filewatcher", DISPATCH_QUEUE_SERIAL);
    }
    return self;
}

- (void)start {
    if (self.stream) return;
    FSEventStreamContext ctx = {0, (__bridge void *)self, NULL, NULL, NULL};
    CFArrayRef cfPaths = (__bridge CFArrayRef)self.paths;
    self.stream = FSEventStreamCreate(kCFAllocatorDefault,
                                      &FWCallback,
                                      &ctx,
                                      cfPaths,
                                      kFSEventStreamEventIdSinceNow,
                                      0.5,
                                      kFSEventStreamCreateFlagFileEvents |
                                      kFSEventStreamCreateFlagUseCFTypes |
                                      kFSEventStreamCreateFlagNoDefer);
    if (!self.stream) return;
    FSEventStreamSetDispatchQueue(self.stream, self.queue);
    FSEventStreamStart(self.stream);
}

- (void)stop {
    if (!self.stream) return;
    FSEventStreamStop(self.stream);
    FSEventStreamInvalidate(self.stream);
    FSEventStreamRelease(self.stream);
    self.stream = NULL;
}

- (void)dealloc {
    [self stop];
}

@end
