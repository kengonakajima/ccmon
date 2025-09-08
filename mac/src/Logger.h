#import <Foundation/Foundation.h>

typedef NS_ENUM(NSInteger, CCLogLevel) {
    CCLogLevelInfo = 0,
    CCLogLevelDebug = 1,
    CCLogLevelError = 2
};

@interface Logger : NSObject
+ (void)setupWithArgc:(int)argc argv:(const char *[])argv;
+ (void)log:(CCLogLevel)level fmt:(NSString *)fmt, ... NS_FORMAT_FUNCTION(2,3);
+ (BOOL)isDebug;
@end

