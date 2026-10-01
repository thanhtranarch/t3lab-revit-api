# -*- coding: utf-8 -*-
"""
Feedback Dialog

Popup window that lets the user send feedback or suggestions about T3Lab
tools. The message is delivered by email to the T3Lab team using a clear,
consistent subject line:

    [T3Lab Feedback][<TYPE>] <USER SUBJECT>

The email opens in the user's default mail client (Outlook, etc.) with
recipient, subject and body already filled in -- the user only needs to
press Send.
"""

from __future__ import unicode_literals

from datetime import datetime
import os

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System import Uri
from System.Diagnostics import Process, ProcessStartInfo
from System.Windows import WindowState, Clipboard

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow

_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'Feedback.xaml')

FEEDBACK_RECIPIENT = "trantienthanh909@gmail.com"
SUBJECT_PREFIX     = "[T3Lab Feedback]"

logger = script.get_logger()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe_get(getter, default=""):
    try:
        value = getter()
        return value if value is not None else default
    except Exception:
        return default


def _revit_context():
    """Collect non-identifying Revit context to include in the feedback body."""
    doc = revit.doc
    app = _safe_get(lambda: doc.Application) if doc else None

    revit_version = _safe_get(lambda: app.VersionNumber, "unknown")
    revit_build   = _safe_get(lambda: app.VersionBuild, "unknown")
    doc_title     = _safe_get(lambda: doc.Title, "<no open document>") if doc else "<no open document>"

    return {
        "revit_version": revit_version,
        "revit_build":   revit_build,
        "doc_title":     doc_title,
        "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _build_subject(feedback_type, user_subject):
    user_subject = (user_subject or "").strip()
    if not user_subject:
        user_subject = "(no subject)"
    return "{prefix}[{ftype}] {subject}".format(
        prefix=SUBJECT_PREFIX,
        ftype=feedback_type,
        subject=user_subject,
    )


def _build_body(feedback_type, message, user_name, user_email, ctx):
    lines = [
        "Feedback type : {}".format(feedback_type),
        "From          : {}".format(user_name or "(anonymous)"),
        "Reply-to      : {}".format(user_email or "(not provided)"),
        "Submitted     : {}".format(ctx["timestamp"]),
        "Revit version : {} (build {})".format(ctx["revit_version"], ctx["revit_build"]),
        "Document      : {}".format(ctx["doc_title"]),
        "",
        "-- Message --",
        message.strip(),
        "",
        "-- Sent from T3Lab feedback tool --",
    ]
    return "\n".join(lines)


def _url_encode(text):
    """Percent-encode a string for use in a mailto: URL."""
    return Uri.EscapeDataString(text or "")


def _open_mailto(to_addr, subject, body):
    """Open the user's default mail client with a prefilled message."""
    mailto = "mailto:{to}?subject={subj}&body={body}".format(
        to=to_addr,
        subj=_url_encode(subject),
        body=_url_encode(body),
    )
    psi = ProcessStartInfo(mailto)
    psi.UseShellExecute = True
    Process.Start(psi)


# ─── Window ──────────────────────────────────────────────────────────────────

class FeedbackWindow(T3WPFWindow):
    """Popup window to collect and send feedback by email."""

    def __init__(self):
        T3WPFWindow.__init__(self, _XAML)
        self.doc = revit.doc

    # -------- chrome / title bar --------
    def minimize_button_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
            self.btn_maximize.ToolTip = "Maximize"
        else:
            self.WindowState = WindowState.Maximized
            self.btn_maximize.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()

    # -------- main action --------
    def _selected_type(self):
        item = self.feedback_type.SelectedItem
        if item is not None and hasattr(item, "Content"):
            return str(item.Content)
        return "Suggestion"

    def _set_status(self, text, error=False):
        self.status_text.Text = text
        self.status_text.Foreground = self.FindResource(
            "T3.Danger.Text" if error else "T3.TextMuted")

    def send_feedback_clicked(self, sender, e):
        message = (self.message_text.Text or "").strip()
        if not message:
            self._set_status("Please write a message before sending.", error=True)
            return

        feedback_type = self._selected_type()
        subject_user  = (self.subject_text.Text or "").strip()
        user_name     = (self.user_name.Text or "").strip()
        user_email    = (self.user_email.Text or "").strip()

        ctx     = _revit_context()
        subject = _build_subject(feedback_type, subject_user)
        body    = _build_body(feedback_type, message, user_name, user_email, ctx)

        try:
            _open_mailto(FEEDBACK_RECIPIENT, subject, body)
            self._set_status("Opened your email client -- please press Send to deliver.")
            forms.alert(
                "Your default email client has opened with the feedback "
                "ready to send to {}.\n\nPlease press Send in that window "
                "to deliver it.".format(FEEDBACK_RECIPIENT),
                title="Feedback ready to send",
            )
            self.Close()
        except Exception as ex:
            logger.error("Could not open mail client: {}".format(ex))
            # Fallback: copy to clipboard so the user can paste it manually
            try:
                clipboard_payload = "To: {}\nSubject: {}\n\n{}".format(
                    FEEDBACK_RECIPIENT, subject, body
                )
                Clipboard.SetText(clipboard_payload)
                forms.alert(
                    "Could not open a mail client automatically.\n\n"
                    "The feedback message has been copied to your clipboard. "
                    "Please paste it into an email to {}.".format(FEEDBACK_RECIPIENT),
                    title="Feedback -- manual send",
                )
                self._set_status("Mail client unavailable -- copied to clipboard.", error=True)
            except Exception as cex:
                logger.error("Clipboard copy also failed: {}".format(cex))
                forms.alert(
                    "Could not open a mail client or copy to clipboard.\n\n"
                    "Please email {} manually.\n\nError: {}".format(
                        FEEDBACK_RECIPIENT, ex
                    ),
                    title="Feedback -- send failed",
                )


def show_feedback_dialog():
    """Show the Feedback dialog."""
    FeedbackWindow().ShowDialog()
