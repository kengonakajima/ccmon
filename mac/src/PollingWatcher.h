#import <Foundation/Foundation.h>

typedef void (^PWEventHandler)(NSString *path);

@interface PollingWatcher : NSObject
- (instancetype)initWithPath:(NSString *)path
                     interval:(NSTimeInterval)interval
                   extensions:(NSArray<NSString *> *)extensions
                      handler:(PWEventHandler)handler;
- (void)start;
- (void)stop;
@end

