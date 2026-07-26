//! Native `alert()` / `confirm()` / `prompt()` for the macOS webview.
//!
//! WKWebView only shows a JavaScript dialog if its UI delegate implements the
//! matching panel callback, and wry implements just the file-open one. Without
//! the other three, WebKit's default kicks in silently: `alert()` does nothing,
//! `confirm()` returns false, `prompt()` returns null. Every confirm-guarded
//! action in Lute — delete a book, archive, delete a term, delete a page —
//! therefore aborted before it did anything, and error alerts never appeared.
//!
//! Rather than fork wry, the three callbacks are added to its delegate class at
//! runtime; that is all WebKit needs to start routing the dialogs here. WebKit
//! calls its UI delegate on the main thread and blocks the page's JavaScript
//! until the completion handler runs, which is what keeps the *synchronous*
//! `confirm()` contract the Lute pages rely on.

use std::ffi::{c_void, CStr};

use block2::Block;
use objc2::ffi::{class_addMethod, object_getClass};
use objc2::runtime::{AnyClass, AnyObject, Bool, Imp, Sel};
use objc2::{msg_send, sel, MainThreadMarker, MainThreadOnly};
use objc2_app_kit::{NSAlert, NSAlertFirstButtonReturn, NSAlertStyle, NSTextField, NSView};
use objc2_foundation::{NSCopying, NSPoint, NSRect, NSSize, NSString};

/// Run a modal alert, returning true if the user chose the first (OK) button.
///
/// `cancel` adds a second, cancelling button; `accessory` is the prompt's text
/// field, when there is one.
fn run_modal(
    message: &NSString,
    cancel: bool,
    accessory: Option<&NSView>,
    mtm: MainThreadMarker,
) -> bool {
    let alert = NSAlert::new(mtm);
    alert.setAlertStyle(NSAlertStyle::Informational);
    alert.setMessageText(message);
    alert.addButtonWithTitle(&NSString::from_str("OK"));
    if cancel {
        alert.addButtonWithTitle(&NSString::from_str("Cancel"));
    }
    if let Some(view) = accessory {
        alert.setAccessoryView(Some(view));
    }
    alert.runModal() == NSAlertFirstButtonReturn
}

/// The dialog text, or an empty string when the page passed nothing.
///
/// Returned by value: the argument is only guaranteed to live for the call, and
/// the alert outlives it by way of the modal loop.
fn message_text(message: *const NSString) -> objc2::rc::Retained<NSString> {
    if message.is_null() {
        NSString::new()
    } else {
        unsafe { &*message }.copy()
    }
}

extern "C" fn run_alert_panel(
    _this: *mut AnyObject,
    _cmd: Sel,
    _webview: *mut AnyObject,
    message: *const NSString,
    _frame: *mut AnyObject,
    handler: *mut Block<dyn Fn()>,
) {
    if let Some(mtm) = MainThreadMarker::new() {
        run_modal(&message_text(message), false, None, mtm);
    }
    // The handler must run even if we somehow could not show the dialog,
    // otherwise the page's JavaScript stays blocked forever.
    if !handler.is_null() {
        unsafe { (*handler).call(()) };
    }
}

extern "C" fn run_confirm_panel(
    _this: *mut AnyObject,
    _cmd: Sel,
    _webview: *mut AnyObject,
    message: *const NSString,
    _frame: *mut AnyObject,
    handler: *mut Block<dyn Fn(Bool)>,
) {
    let mut ok = false;
    if let Some(mtm) = MainThreadMarker::new() {
        ok = run_modal(&message_text(message), true, None, mtm);
    }
    if !handler.is_null() {
        unsafe { (*handler).call((Bool::new(ok),)) };
    }
}

extern "C" fn run_text_input_panel(
    _this: *mut AnyObject,
    _cmd: Sel,
    _webview: *mut AnyObject,
    prompt: *const NSString,
    default_text: *const NSString,
    _frame: *mut AnyObject,
    handler: *mut Block<dyn Fn(*const NSString)>,
) {
    let mut answer = None;
    if let Some(mtm) = MainThreadMarker::new() {
        let field = NSTextField::initWithFrame(
            NSTextField::alloc(mtm),
            NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(280.0, 24.0)),
        );
        field.setStringValue(&message_text(default_text));
        if run_modal(&message_text(prompt), true, Some(&field), mtm) {
            answer = Some(field.stringValue());
        }
    }
    if !handler.is_null() {
        // Cancelling is reported as a null string, which is what the page sees
        // as `prompt()` returning null.
        let value = answer
            .as_deref()
            .map_or(std::ptr::null(), |s| s as *const NSString);
        unsafe { (*handler).call((value,)) };
    }
}

/// Add `imp` to `cls` under `sel`, unless the class already answers to it.
///
/// # Safety
///
/// `imp` must be a function matching the selector's calling convention, and
/// `types` its ObjC type encoding.
unsafe fn add_method(cls: *mut AnyClass, sel: Sel, imp: *const c_void, types: &CStr) {
    let imp: Imp = std::mem::transmute(imp);
    // Fails harmlessly if a future wry implements the method itself, in which
    // case its version stays in place.
    class_addMethod(cls, sel, imp, types.as_ptr());
}

/// Teach wry's UI delegate to show JavaScript dialogs.
///
/// `webview` is the `WKWebView` pointer from Tauri's `with_webview`.
pub fn install(webview: *mut c_void) {
    if webview.is_null() {
        return;
    }
    let webview = webview as *mut AnyObject;

    unsafe {
        let delegate: *mut AnyObject = msg_send![webview, UIDelegate];
        if delegate.is_null() {
            return;
        }
        let cls = object_getClass(delegate) as *mut AnyClass;

        // "v@:@@@@?" - void return; self, _cmd, webview, message, frame, block.
        let alert: extern "C" fn(
            *mut AnyObject,
            Sel,
            *mut AnyObject,
            *const NSString,
            *mut AnyObject,
            *mut Block<dyn Fn()>,
        ) = run_alert_panel;
        add_method(
            cls,
            sel!(webView:runJavaScriptAlertPanelWithMessage:initiatedByFrame:completionHandler:),
            alert as *const c_void,
            c"v@:@@@@?",
        );

        let confirm: extern "C" fn(
            *mut AnyObject,
            Sel,
            *mut AnyObject,
            *const NSString,
            *mut AnyObject,
            *mut Block<dyn Fn(Bool)>,
        ) = run_confirm_panel;
        add_method(
            cls,
            sel!(webView:runJavaScriptConfirmPanelWithMessage:initiatedByFrame:completionHandler:),
            confirm as *const c_void,
            c"v@:@@@@?",
        );

        // The text input panel takes an extra argument, the default text.
        let text_input: extern "C" fn(
            *mut AnyObject,
            Sel,
            *mut AnyObject,
            *const NSString,
            *const NSString,
            *mut AnyObject,
            *mut Block<dyn Fn(*const NSString)>,
        ) = run_text_input_panel;
        add_method(
            cls,
            sel!(webView:runJavaScriptTextInputPanelWithPrompt:defaultText:initiatedByFrame:completionHandler:),
            text_input as *const c_void,
            c"v@:@@@@@?",
        );

        // WebKit snapshots which callbacks the delegate implements when the
        // delegate is assigned, and wry assigned this one while the webview was
        // being built. Without re-assigning it, the methods just added are
        // never consulted and the dialogs stay invisible.
        let none: *mut AnyObject = std::ptr::null_mut();
        let _: () = msg_send![webview, setUIDelegate: none];
        let _: () = msg_send![webview, setUIDelegate: delegate];

        // wry keeps its own strong reference to the delegate, so the weak
        // `UIDelegate` property surviving the round trip is what tells us the
        // re-assignment took rather than silently clearing the delegate.
        let restored: *mut AnyObject = msg_send![webview, UIDelegate];
        if restored != delegate {
            eprintln!("lute: could not reinstall the webview UI delegate; JavaScript dialogs will not appear");
        }
    }
}
