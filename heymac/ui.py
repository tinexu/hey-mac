"""Spotlight-style floating bar + menu bar icon (native AppKit via PyObjC).

All methods on SpotlightBar must be called on the main thread
(use PyObjCTools.AppHelper.callAfter from other threads).
"""
from __future__ import annotations

import os
from typing import Callable

import objc
from AppKit import (
    NSAnimationContext, NSBackingStoreBuffered, NSBezierPath, NSButton, NSColor, NSFont,
    NSFloatingWindowLevel, NSMakeRect, NSMenu, NSMenuItem, NSObject, NSPanel, NSScreen,
    NSStatusBar, NSTextField, NSView, NSVisualEffectView, NSImage, NSLineBreakByTruncatingMiddle,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel, NSVariableStatusItemLength,
)
from Foundation import NSTimer

WIDTH = 640
TOP_H = 64
MSG_H = 24
ITEM_H = 28
PAD = 14

IDLE, LISTENING, THINKING, DONE, ERROR = range(5)


class KeyPanel(NSPanel):
    """Borderless panel that can still take keyboard focus (like Spotlight)."""

    def canBecomeKeyWindow(self):
        return True


class FlippedView(NSView):
    def isFlipped(self):
        return True


class Orb(NSView):
    """The little circle on the left: pulses with your voice, changes colour by state."""

    def initWithFrame_(self, frame):
        self = objc.super(Orb, self).initWithFrame_(frame)
        if self is None:
            return None
        self.level = 0.0
        self.state = IDLE
        return self

    def drawRect_(self, rect):
        b = self.bounds()
        colors = {
            IDLE: NSColor.tertiaryLabelColor(),
            LISTENING: NSColor.systemPinkColor(),
            THINKING: NSColor.systemBlueColor(),
            DONE: NSColor.systemGreenColor(),
            ERROR: NSColor.systemOrangeColor(),
        }
        base = b.size.width * 0.42
        r = base + (b.size.width / 2 - base) * (self.level if self.state == LISTENING else 0)
        cx, cy = b.size.width / 2, b.size.height / 2
        if self.state == LISTENING:
            colors[LISTENING].colorWithAlphaComponent_(0.25).set()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(0, 0, b.size.width, b.size.height)).fill()
        colors[self.state].set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r * 0.6, cy - r * 0.6, r * 1.2, r * 1.2)).fill()


class SpotlightBar(NSObject):
    def initWithName_onSubmit_onOpenItem_onTyping_(self, name, on_submit, on_open_item, on_typing):
        self = objc.super(SpotlightBar, self).init()
        if self is None:
            return None
        self.name = name
        self.on_submit: Callable[[str], None] = on_submit
        self.on_open_item: Callable[[str, bool], None] = on_open_item
        self.on_typing: Callable[[], None] = on_typing
        self.items: list[str] = []
        self.item_buttons = []
        self.hide_timer = None
        self.took_focus = False
        self._build()
        return self

    # ---------------------------------------------------------------- build
    @objc.python_method
    def _build(self):
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = KeyPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, TOP_H), style, NSBackingStoreBuffered, False)
        panel.setLevel_(NSFloatingWindowLevel + 1)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setMovableByWindowBackground_(True)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setDelegate_(self)

        blur = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, TOP_H))
        blur.setMaterial_(13)          # NSVisualEffectMaterialHUDWindow
        blur.setBlendingMode_(0)       # behind window
        blur.setState_(1)              # always active
        blur.setWantsLayer_(True)
        blur.layer().setCornerRadius_(16)
        blur.layer().setMasksToBounds_(True)
        blur.setAutoresizingMask_(18)  # width + height sizable
        panel.setContentView_(blur)

        root = FlippedView.alloc().initWithFrame_(blur.bounds())
        root.setAutoresizingMask_(18)
        blur.addSubview_(root)
        self.root = root

        self.orb = Orb.alloc().initWithFrame_(NSMakeRect(PAD + 4, (TOP_H - 28) / 2, 28, 28))
        root.addSubview_(self.orb)

        field = NSTextField.alloc().initWithFrame_(NSMakeRect(60, (TOP_H - 30) / 2, WIDTH - 60 - PAD, 30))
        field.setFont_(NSFont.systemFontOfSize_weight_(22, 0.0))
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setFocusRingType_(1)     # none
        field.setPlaceholderString_(f"Ask {self.name}…")
        field.setDelegate_(self)
        field.cell().setWraps_(False)
        field.cell().setScrollable_(True)
        root.addSubview_(field)
        self.field = field

        msg = NSTextField.labelWithString_("")
        msg.setFrame_(NSMakeRect(60, TOP_H - 4, WIDTH - 60 - PAD, MSG_H))
        msg.setFont_(NSFont.systemFontOfSize_(14))
        msg.setTextColor_(NSColor.secondaryLabelColor())
        msg.setLineBreakMode_(NSLineBreakByTruncatingMiddle)
        root.addSubview_(msg)
        self.msg = msg

        self.panel = panel

    # ------------------------------------------------------------- layout
    @objc.python_method
    def _relayout(self):
        n = len(self.items)
        has_msg = bool(self.msg.stringValue())
        h = TOP_H + (MSG_H + 6 if has_msg else 0) + (n * ITEM_H + 8 if n else 0)
        self.msg.setHidden_(not has_msg)
        for i, b in enumerate(self.item_buttons):
            b.setFrame_(NSMakeRect(52, TOP_H + MSG_H + 6 + i * ITEM_H, WIDTH - 52 - PAD, ITEM_H))
        f = self.panel.frame()
        top = f.origin.y + f.size.height
        self.panel.setFrame_display_(NSMakeRect(f.origin.x, top - h, WIDTH, h), True)

    @objc.python_method
    def _center(self):
        screen = NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + (screen.size.width - WIDTH) / 2
        top = screen.origin.y + screen.size.height * 0.78
        self.panel.setFrame_display_(NSMakeRect(x, top - TOP_H, WIDTH, TOP_H), True)

    # ----------------------------------------------------------- public API
    def isVisible(self):
        return self.panel.isVisible()

    def showListening_(self, take_focus):
        self._cancel_hide()
        if not self.panel.isVisible():
            self._center()
        self.set_items([])
        self.field.setStringValue_("")
        self.field.setPlaceholderString_("Listening… (or just type)")
        self.msg.setStringValue_("")
        self.orb.state = LISTENING
        self.orb.level = 0
        self.orb.setNeedsDisplay_(True)
        self._relayout()
        self.panel.setAlphaValue_(1.0)
        if take_focus:
            self.took_focus = True
            self.panel.makeKeyAndOrderFront_(None)
            self.panel.makeFirstResponder_(self.field)
        else:
            self.took_focus = False
            self.panel.orderFrontRegardless()

    def setLevel_(self, level):
        self.orb.level = 0.6 * self.orb.level + 0.4 * float(level)
        self.orb.setNeedsDisplay_(True)

    def setQuery_(self, text):
        self.field.setStringValue_(text)

    def showThinking_(self, text):
        self._cancel_hide()
        self.orb.state = THINKING
        self.orb.setNeedsDisplay_(True)
        self.msg.setStringValue_(text or "Thinking…")
        self._relayout()

    def showResult_items_ok_(self, text, items, ok):
        self.orb.state = DONE if ok else ERROR
        self.orb.level = 0
        self.orb.setNeedsDisplay_(True)
        self.field.setPlaceholderString_(f"Ask {self.name}…")
        self.msg.setStringValue_(text)
        self.set_items(list(items or []))
        self._relayout()
        self._schedule_hide(15.0 if items else 3.5)

    @objc.python_method
    def set_items(self, items):
        for b in self.item_buttons:
            b.removeFromSuperview()
        self.item_buttons = []
        self.items = items
        home = os.path.expanduser("~")
        for i, path in enumerate(items):
            label = f"{i + 1}   {os.path.basename(path.rstrip('/'))}   —   {os.path.dirname(path).replace(home, '~')}"
            b = NSButton.buttonWithTitle_target_action_(label, self, "itemClicked:")
            b.setBordered_(False)
            b.setAlignment_(0)   # left
            b.setFont_(NSFont.systemFontOfSize_(14))
            b.setTag_(i)
            b.cell().setLineBreakMode_(NSLineBreakByTruncatingMiddle)
            b.setToolTip_(path)
            self.root.addSubview_(b)
            self.item_buttons.append(b)

    def itemClicked_(self, sender):
        from AppKit import NSEvent, NSEventModifierFlagCommand
        reveal = bool(NSEvent.modifierFlags() & NSEventModifierFlagCommand)
        self.on_open_item(self.items[sender.tag()], reveal)
        self.hide()

    def hide(self):
        self._cancel_hide()
        self.took_focus = False
        self.panel.orderOut_(None)
        self.orb.state = IDLE

    # -------------------------------------------------------- auto-hide
    @objc.python_method
    def _schedule_hide(self, seconds):
        self._cancel_hide()
        self.hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            seconds, self, "fadeOut:", None, False)

    @objc.python_method
    def _cancel_hide(self):
        if self.hide_timer is not None:
            self.hide_timer.invalidate()
            self.hide_timer = None

    def fadeOut_(self, timer):
        self.hide_timer = None
        if self.panel.isKeyWindow() and self.field.stringValue() and self.orb.state == IDLE:
            return
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.25)
        self.panel.animator().setAlphaValue_(0.0)
        NSAnimationContext.endGrouping()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.3, self, "finishFade:", None, False)

    def finishFade_(self, timer):
        if self.panel.alphaValue() < 0.05:
            self.hide()
            self.panel.setAlphaValue_(1.0)

    # ------------------------------------------------ text field delegate
    def controlTextDidChange_(self, note):
        self._cancel_hide()
        self.orb.state = IDLE
        self.orb.setNeedsDisplay_(True)
        self.on_typing()

    @objc.typedSelector(b"Z@:@@:")
    def control_textView_doCommandBySelector_(self, control, textView, selector):
        if isinstance(selector, bytes):
            selector = selector.decode()
        if selector == "insertNewline:":
            text = self.field.stringValue().strip()
            if text:
                self.on_submit(text)
            return True
        if selector == "cancelOperation:":        # Esc
            self.hide()
            return True
        return False

    # --------------------------------------------------- window delegate
    def windowDidResignKey_(self, note):
        if self.took_focus and self.orb.state != THINKING:
            self.hide()


class MenuBar(NSObject):
    """Menu bar icon with a small menu."""

    def initWithActions_(self, actions):
        self = objc.super(MenuBar, self).init()
        if self is None:
            return None
        self.actions = actions          # list of (title, callable) ; None = separator
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_("waveform", "Assistant")
        if img:
            img.setTemplate_(True)
            self.item.button().setImage_(img)
        else:
            self.item.button().setTitle_("◉")
        self.menu = NSMenu.alloc().init()
        self.status_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Starting…", None, "")
        self.status_item.setEnabled_(False)
        self.set_actions(actions)
        self.item.setMenu_(self.menu)
        return self

    @objc.python_method
    def set_actions(self, actions):
        """(Re)build the menu below the status line, e.g. after the config is reloaded."""
        self.actions = actions
        self.menu.removeAllItems()
        self.menu.addItem_(self.status_item)
        self.menu.addItem_(NSMenuItem.separatorItem())
        for i, entry in enumerate(actions):
            if entry is None:
                self.menu.addItem_(NSMenuItem.separatorItem())
                continue
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(entry[0], "menuClicked:", "")
            mi.setTarget_(self)
            mi.setTag_(i)
            self.menu.addItem_(mi)

    def menuClicked_(self, sender):
        self.actions[sender.tag()][1]()

    def setStatus_(self, text):
        self.status_item.setTitle_(text)
