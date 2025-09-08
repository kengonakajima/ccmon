#import <Cocoa/Cocoa.h>
#import "AppDelegate.h"
#import "Logger.h"

static AppDelegate *sAppDelegate; // 強参照で保持

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        [Logger setupWithArgc:argc argv:argv];
        [Logger log:CCLogLevelInfo fmt:@"ccMonMac starting up"];    
        NSApplication *app = [NSApplication sharedApplication];
        NSDictionary *env = [[NSProcessInfo processInfo] environment];
        BOOL dockMode = [env[@"CCMON_DOCK"] isEqualToString:@"1"] || [env[@"CCMON_UI"] isEqualToString:@"regular"];
        [app setActivationPolicy:dockMode ? NSApplicationActivationPolicyRegular : NSApplicationActivationPolicyAccessory];
        [Logger log:CCLogLevelInfo fmt:@"activation policy = %@", dockMode ? @"Regular(Dock visible)" : @"Accessory(Menu bar only)"];    
        sAppDelegate = [AppDelegate new];
        app.delegate = sAppDelegate;
        [Logger log:CCLogLevelInfo fmt:@"NSApplication configured, entering run loop"];    
        [app run];
        [Logger log:CCLogLevelInfo fmt:@"run loop exited"];    
        return 0;
    }
}
