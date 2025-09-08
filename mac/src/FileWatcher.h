#import <Foundation/Foundation.h>
#import <CoreServices/CoreServices.h>

typedef void (^FWEventHandler)(NSString *path, FSEventStreamEventFlags flags);

@interface FileWatcher : NSObject
- (instancetype)initWithPaths:(NSArray<NSString *> *)paths handler:(FWEventHandler)handler;
- (void)start;
- (void)stop;
@end

