"""Floating glowing lily (or a little animal, see critters.py) in a corner of the screen.

Blooms when the assistant starts listening, pulses with your voice, turns while
thinking, then closes and disappears. Click it to dismiss. A small bubble above it
shows what it heard and the result.

Same public methods as ui.SpotlightBar so app.py can use either.
All methods must be called on the main thread.
"""
from __future__ import annotations

import math
import os
import time

import objc
from AppKit import (
    NSAffineTransform, NSBackingStoreBuffered, NSBezierPath, NSButton, NSColor, NSFont,
    NSFloatingWindowLevel, NSGradient, NSGraphicsContext, NSLineBreakByTruncatingMiddle,
    NSLineBreakByTruncatingTail, NSLineBreakByWordWrapping, NSMakePoint, NSMakeRect, NSObject,
    NSPanel, NSScreen, NSTextField, NSView, NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSTimer

from . import critters, palettes

IDLE, LISTENING, THINKING, DONE, ERROR = range(5)

SIZE = 150          # lily window (square)
MARGIN = 10         # distance from the screen edge
BUBBLE_W = 300
PAD = 12
ITEM_H = 24

GLOW = {
    IDLE: (1.00, 0.55, 0.78),
    LISTENING: (1.00, 0.38, 0.72),
    THINKING: (0.60, 0.45, 1.00),
    DONE: (0.40, 0.90, 0.60),
    ERROR: (1.00, 0.66, 0.25),
}


def rgba(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


def _panel(w, h, cls=None):
    p = (cls or NoKeyPanel).alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered, False)
    p.setLevel_(NSFloatingWindowLevel + 1)
    p.setOpaque_(False)
    p.setBackgroundColor_(NSColor.clearColor())
    p.setHidesOnDeactivate_(False)
    p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                             | NSWindowCollectionBehaviorFullScreenAuxiliary)
    return p


class NoKeyPanel(NSPanel):
    """Never steals keyboard focus from whatever you're typing in."""

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class TypingPanel(NSPanel):
    """Bubble panel: takes keyboard focus only when you opened it with the hotkey."""

    def canBecomeKeyWindow(self):
        return bool(getattr(self, "allow_key", False))

    def canBecomeMainWindow(self):
        return False


class AppKitPen:
    """The drawing calls critters.py needs, done with AppKit (y up, degrees)."""

    def save(self):
        NSGraphicsContext.saveGraphicsState()

    def restore(self):
        NSGraphicsContext.restoreGraphicsState()

    def _xf(self, fn):
        t = NSAffineTransform.transform()
        fn(t)
        t.concat()

    def translate(self, x, y):
        self._xf(lambda t: t.translateXBy_yBy_(x, y))

    def rotate(self, deg):
        self._xf(lambda t: t.rotateByDegrees_(deg))

    def scale(self, sx, sy):
        self._xf(lambda t: t.scaleXBy_yBy_(sx, sy))

    def _paint(self, path, fill, stroke, width, alpha, stroke_alpha):
        if fill:
            rgba(*fill, alpha).set()
            path.fill()
        if stroke:
            rgba(*stroke, stroke_alpha).set()
            path.setLineWidth_(width)
            path.setLineCapStyle_(1)        # round
            path.setLineJoinStyle_(1)
            path.stroke()

    def ellipse(self, x, y, rx, ry, fill=None, stroke=None, width=1.0, alpha=1.0, stroke_alpha=1.0):
        path = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - rx, y - ry, 2 * rx, 2 * ry))
        self._paint(path, fill, stroke, width, alpha, stroke_alpha)

    def shape(self, cmds, fill=None, stroke=None, width=1.0, alpha=1.0, stroke_alpha=1.0):
        path = NSBezierPath.bezierPath()
        for c in cmds:
            if c[0] == "M":
                path.moveToPoint_(NSMakePoint(c[1], c[2]))
            elif c[0] == "L":
                path.lineToPoint_(NSMakePoint(c[1], c[2]))
            elif c[0] == "C":
                path.curveToPoint_controlPoint1_controlPoint2_(
                    NSMakePoint(c[5], c[6]), NSMakePoint(c[1], c[2]), NSMakePoint(c[3], c[4]))
            else:
                path.closePath()
        self._paint(path, fill, stroke, width, alpha, stroke_alpha)

    def glow(self, x, y, radius, color, strength):
        r, g, b = color
        grad = NSGradient.alloc().initWithColors_([
            rgba(r, g, b, 0.45 + 0.5 * strength), rgba(r, g, b, 0.30 * strength + 0.1), rgba(r, g, b, 0.0)])
        grad.drawFromCenter_radius_toCenter_radius_options_(NSMakePoint(x, y), 0, NSMakePoint(x, y), radius, 0)


class LilyFlippedView(NSView):
    def isFlipped(self):
        return True


class LilyView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(LilyView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.state = IDLE
        self.level = 0.0       # voice loudness 0..1
        self.bloom = 0.0       # 0 = closed bud, 1 = fully open
        self.phase = 0.0       # animation clock
        self.on_click = None
        self.palette = palettes.resolve(palettes.DEFAULT)
        self.animal = "lily"   # or cat / bunny / frog / bear / fox / otter / owl
        self.pen = AppKitPen()
        return self

    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        if self.on_click:
            self.on_click()

    # ------------------------------------------------------------ drawing
    def drawRect_(self, rect):
        o = self.bloom
        if o <= 0.01:
            return
        b = self.bounds()
        cx, cy = b.size.width / 2, b.size.height / 2
        pal = self.palette
        if self.animal in critters.ANIMALS:
            self.pen.save()
            self.pen.translate(cx, cy)
            critters.draw(self.pen, self.animal, self.state, self.level, o, self.phase, pal)
            self.pen.restore()
            return
        if self.state in (IDLE, LISTENING):
            r, g, bl = pal["glow"]
        elif self.state == THINKING:
            r, g, bl = pal["think"]
        else:
            r, g, bl = GLOW[self.state]          # green = done, amber = error, in every palette
        pulse = 0.5 + 0.5 * math.sin(self.phase * 2.4)

        if self.state == LISTENING:
            strength = 0.35 + 0.65 * self.level
        elif self.state == THINKING:
            strength = 0.35 + 0.45 * pulse
        else:
            strength = 0.55

        # soft glow behind the flower
        radius = (50 + 24 * strength) * o
        glow = NSGradient.alloc().initWithColors_([
            rgba(r, g, bl, 0.45 + 0.5 * strength), rgba(r, g, bl, 0.30 * strength + 0.1), rgba(r, g, bl, 0.0)])
        glow.drawFromCenter_radius_toCenter_radius_options_(
            NSMakePoint(cx, cy), 0, NSMakePoint(cx, cy), radius, 0)

        # petals: lilies have 6 — three behind, three in front
        length = 44 * (0.35 + 0.65 * o) * (1 + (0.07 * self.level if self.state == LISTENING else 0))
        spread = 0.25 + 0.75 * o            # petals fan out as it blooms
        if self.state == THINKING:
            spin = self.phase * 40
        else:
            spin = math.sin(self.phase * 0.9) * 4
        (bb, bt), (fb, ft) = pal["back"], pal["front"]
        self._petals(cx, cy, length * 0.93, 60 + spin, spread, rgba(*bb), rgba(*bt), bb)
        self._petals(cx, cy, length, 0 + spin, spread, rgba(*fb), rgba(*ft), fb)
        self._stamens(cx, cy, length, spin, o)

    @objc.python_method
    def _petal_path(self, length, width):
        p = NSBezierPath.bezierPath()
        p.moveToPoint_(NSMakePoint(0, 0))
        p.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(0, length), NSMakePoint(width, length * 0.30), NSMakePoint(width * 0.55, length * 0.82))
        p.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(0, 0), NSMakePoint(-width * 0.55, length * 0.82), NSMakePoint(-width, length * 0.30))
        p.closePath()
        return p

    @objc.python_method
    def _petals(self, cx, cy, length, start_deg, spread, base, tip, edge_rgb):
        grad = NSGradient.alloc().initWithStartingColor_endingColor_(base, tip)
        for k in range(3):
            angle = start_deg + k * 120
            NSGraphicsContext.saveGraphicsState()
            t = NSAffineTransform.transform()
            t.translateXBy_yBy_(cx, cy)
            t.rotateByDegrees_(angle)
            t.scaleXBy_yBy_(spread, 1.0)
            t.concat()
            path = self._petal_path(length, length * 0.36)
            grad.drawInBezierPath_angle_(path, 90)
            er, eg, eb = (max(0.0, c * 0.85) for c in edge_rgb)
            rgba(er, eg, eb, 0.35).set()
            path.setLineWidth_(0.6)
            path.stroke()
            vein = NSBezierPath.bezierPath()                      # centre vein
            vein.moveToPoint_(NSMakePoint(0, length * 0.08))
            vein.lineToPoint_(NSMakePoint(0, length * 0.72))
            vein.setLineWidth_(0.8)
            rgba(er, eg, eb, 0.45).set()
            vein.stroke()
            NSGraphicsContext.restoreGraphicsState()

    @objc.python_method
    def _stamens(self, cx, cy, length, spin, o):
        if o < 0.4:
            return
        a = (o - 0.4) / 0.6
        for k in range(6):
            ang = math.radians(90 + 30 + k * 60 + spin)
            L = length * 0.55 * a
            x, y = cx + math.cos(ang) * L, cy + math.sin(ang) * L
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(cx, cy))
            line.lineToPoint_(NSMakePoint(x, y))
            line.setLineWidth_(1.0)
            rgba(*self.palette["filament"], 0.9).set()
            line.stroke()
            rgba(*self.palette["stamen"]).set()                   # pollen tip
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 2.5, y - 2.5, 5, 5)).fill()
        rgba(*self.palette["center"]).set()                        # centre
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 4, cy - 4, 8, 8)).fill()


class LilyOverlay(NSObject):
    def initWithName_corner_onSubmit_onOpenItem_onTyping_onDismiss_(
            self, name, corner, on_submit, on_open_item, on_typing, on_dismiss):
        self = objc.super(LilyOverlay, self).init()
        if self is None:
            return None
        self.name = name
        self.corner = corner or "bottom-right"
        self.on_open_item = on_open_item
        self.on_dismiss = on_dismiss
        self.on_submit = on_submit
        self.on_typing = on_typing
        self.typing_mode = False
        self.busy = False
        self.query = ""
        self.message = ""
        self.items = []
        self.item_buttons = []
        self.target_bloom = 0.0
        self.close_at = None
        self.timer = None
        self.last_tick = time.time()
        self._build()
        return self

    # --------------------------------------------------------------- build
    @objc.python_method
    def _build(self):
        self.lily_panel = _panel(SIZE, SIZE)
        self.lily = LilyView.alloc().initWithFrame_(NSMakeRect(0, 0, SIZE, SIZE))
        self.lily.on_click = self.dismiss
        self.lily_panel.setContentView_(self.lily)

        self.bubble_panel = _panel(BUBBLE_W, 60, TypingPanel)
        self.bubble_panel.allow_key = False
        self.bubble_panel.setDelegate_(self)
        self.bubble_panel.setHasShadow_(True)
        blur = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, BUBBLE_W, 60))
        blur.setMaterial_(13)          # HUD
        blur.setBlendingMode_(0)
        blur.setState_(1)
        blur.setWantsLayer_(True)
        blur.layer().setCornerRadius_(14)
        blur.layer().setMasksToBounds_(True)
        blur.setAutoresizingMask_(18)
        self.bubble_panel.setContentView_(blur)
        root = LilyFlippedView.alloc().initWithFrame_(blur.bounds())
        root.setAutoresizingMask_(18)
        blur.addSubview_(root)
        self.bubble_root = root

        field = NSTextField.alloc().initWithFrame_(NSMakeRect(PAD, PAD, BUBBLE_W - 2 * PAD, 24))
        field.setFont_(NSFont.systemFontOfSize_(15))
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setFocusRingType_(1)
        field.setPlaceholderString_("Type, or just talk…")
        field.setDelegate_(self)
        field.cell().setWraps_(False)
        field.cell().setScrollable_(True)
        field.setHidden_(True)
        root.addSubview_(field)
        self.field = field

        self.query_label = NSTextField.labelWithString_("")
        self.query_label.setFont_(NSFont.systemFontOfSize_(12))
        self.query_label.setTextColor_(NSColor.secondaryLabelColor())
        self.query_label.setLineBreakMode_(NSLineBreakByTruncatingTail)
        root.addSubview_(self.query_label)

        self.msg_label = NSTextField.wrappingLabelWithString_("")
        self.msg_label.setFont_(NSFont.systemFontOfSize_weight_(14, 0.2))
        self.msg_label.setLineBreakMode_(NSLineBreakByWordWrapping)
        self.msg_label.setMaximumNumberOfLines_(4)
        root.addSubview_(self.msg_label)

    # ------------------------------------------------------------ geometry
    @objc.python_method
    def _lily_origin(self):
        vf = NSScreen.mainScreen().visibleFrame()
        right = "right" in self.corner
        top = "top" in self.corner
        x = vf.origin.x + vf.size.width - SIZE - MARGIN if right else vf.origin.x + MARGIN
        y = vf.origin.y + vf.size.height - SIZE - MARGIN if top else vf.origin.y + MARGIN
        return x, y

    @objc.python_method
    def _layout_bubble(self):
        if not (self.typing_mode or self.query or self.message or self.items):
            self.bubble_panel.orderOut_(None)
            return
        inner = BUBBLE_W - 2 * PAD
        y = PAD
        self.field.setHidden_(not self.typing_mode)
        if self.typing_mode:
            self.field.setFrame_(NSMakeRect(PAD, y - 2, inner, 24))
            y += 28
        show_query = bool(self.query) and not self.typing_mode
        self.query_label.setHidden_(not show_query)
        if show_query:
            self.query_label.setStringValue_(f"“{self.query}”")
            self.query_label.setFrame_(NSMakeRect(PAD, y, inner, 16))
            y += 20
        self.msg_label.setHidden_(not self.message)
        if self.message:
            self.msg_label.setStringValue_(self.message)
            h = self.msg_label.cell().cellSizeForBounds_(NSMakeRect(0, 0, inner, 1000)).height
            h = min(h, 80)
            self.msg_label.setFrame_(NSMakeRect(PAD, y, inner, h))
            y += h + 4
        for i, b in enumerate(self.item_buttons):
            b.setFrame_(NSMakeRect(PAD - 4, y + i * ITEM_H, inner + 4, ITEM_H))
        y += len(self.item_buttons) * ITEM_H + PAD - 4
        height = max(y, 36)

        lx, ly = self._lily_origin()
        right = "right" in self.corner
        top = "top" in self.corner
        bx = lx + SIZE - BUBBLE_W - 16 if right else lx + 16
        by = ly - height + 18 if top else ly + SIZE - 18
        self.bubble_panel.setFrame_display_(NSMakeRect(bx, by, BUBBLE_W, height), True)
        self.bubble_panel.orderFrontRegardless()

    @objc.python_method
    def _set_items(self, items):
        for b in self.item_buttons:
            b.removeFromSuperview()
        self.item_buttons = []
        self.items = list(items)
        home = os.path.expanduser("~")
        for i, path in enumerate(self.items):
            name = os.path.basename(str(path).rstrip("/"))
            where = os.path.dirname(str(path)).replace(home, "~")
            b = NSButton.buttonWithTitle_target_action_(f"{i + 1}  {name}  ·  {where}", self, "itemClicked:")
            b.setBordered_(False)
            b.setAlignment_(0)
            b.setFont_(NSFont.systemFontOfSize_(13))
            b.setTag_(i)
            b.cell().setLineBreakMode_(NSLineBreakByTruncatingMiddle)
            b.setToolTip_(str(path))
            self.bubble_root.addSubview_(b)
            self.item_buttons.append(b)

    def itemClicked_(self, sender):
        from AppKit import NSEvent, NSEventModifierFlagCommand
        reveal = bool(NSEvent.modifierFlags() & NSEventModifierFlagCommand)
        self.on_open_item(self.items[sender.tag()], reveal)
        self.hide()

    # ----------------------------------------------------------- animation
    @objc.python_method
    def _start_timer(self):
        if self.timer is None:
            self.last_tick = time.time()
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1 / 40, self, "tick:", None, True)

    def tick_(self, timer):
        now = time.time()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now
        v = self.lily
        v.phase += dt
        speed = 3.2 if self.target_bloom > v.bloom else 4.0          # bloom / close speed
        if v.bloom < self.target_bloom:
            v.bloom = min(self.target_bloom, v.bloom + dt * speed)
        elif v.bloom > self.target_bloom:
            v.bloom = max(self.target_bloom, v.bloom - dt * speed)
        if v.state != LISTENING:
            v.level *= 0.85
        v.setNeedsDisplay_(True)

        if self.close_at is not None and now >= self.close_at:
            self.close_at = None
            self.hide()
        if self.target_bloom == 0 and v.bloom <= 0.01:
            self.lily_panel.orderOut_(None)
            v.state = IDLE
            self.timer.invalidate()
            self.timer = None

    # ---------------------------------------------------------- public API
    def isVisible(self):
        return self.lily_panel.isVisible() and self.target_bloom > 0

    def showListening_(self, take_focus):
        x, y = self._lily_origin()
        self.lily_panel.setFrameOrigin_(NSMakePoint(x, y))
        self.query, self.message = "", ""
        self._set_items([])
        self.busy = False
        self.lily.state = LISTENING
        self.target_bloom = 1.0
        self.close_at = None
        self.lily_panel.orderFrontRegardless()
        self._start_timer()
        self.typing_mode = bool(take_focus)
        self.bubble_panel.allow_key = self.typing_mode
        self.field.setStringValue_("")
        self._layout_bubble()
        if self.typing_mode:
            self.bubble_panel.makeKeyAndOrderFront_(None)
            self.bubble_panel.makeFirstResponder_(self.field)

    def setPalette_(self, spec):
        """Recolor the lily ('coffee', 'matcha', or a JSON {petal, tip, glow})."""
        self.lily.palette = palettes.resolve(str(spec))
        self.lily.setNeedsDisplay_(True)

    def setMascot_(self, name):
        """'fox', 'owl'… or 'lily' for the flower."""
        name = str(name)
        self.lily.animal = name if name in critters.ANIMALS else "lily"
        self.lily.setNeedsDisplay_(True)

    def setLevel_(self, level):
        self.lily.level = 0.6 * self.lily.level + 0.4 * float(level)

    def setQuery_(self, text):
        self.query = str(text)
        if self.typing_mode:
            self.field.setStringValue_(self.query)
        self._layout_bubble()

    def showThinking_(self, text):
        self.busy = True
        self.lily.state = THINKING
        self.target_bloom = 1.0
        self.close_at = None
        self.message = str(text or "")
        self._start_timer()
        self._layout_bubble()

    def showResult_items_ok_(self, text, items, ok):
        self.busy = False
        self.lily.state = DONE if ok else ERROR
        self.message = str(text or "")
        self._set_items(list(items or []))
        self._layout_bubble()
        self.close_at = time.time() + (12.0 if self.items else 3.0)
        self._start_timer()

    def hide(self):
        self.target_bloom = 0.0
        self.close_at = None
        self.typing_mode = False
        self.bubble_panel.allow_key = False
        self.bubble_panel.orderOut_(None)
        self._start_timer()

    def dismiss(self):
        """Clicked the lily: stop listening and close."""
        if self.on_dismiss:
            self.on_dismiss()
        self.hide()

    # ------------------------------------------------- typing (text field)
    def controlTextDidChange_(self, note):
        self.close_at = None                 # you're typing — don't vanish
        if self.lily.state == LISTENING:
            self.lily.state = IDLE
        self.on_typing()

    @objc.typedSelector(b"Z@:@@:")
    def control_textView_doCommandBySelector_(self, control, textView, selector):
        if isinstance(selector, bytes):
            selector = selector.decode()
        if selector == "insertNewline:":
            text = self.field.stringValue().strip()
            if text:
                self.query = text
                self.on_submit(text)
            return True
        if selector == "cancelOperation:":   # Esc
            self.dismiss()
            return True
        return False

    def windowDidResignKey_(self, note):
        # clicked somewhere else while the text box was open
        if self.typing_mode and not self.busy and not self.items:
            self.dismiss()