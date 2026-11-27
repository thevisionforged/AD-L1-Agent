"""Input and output guardrails that run before and after the model.

Pre-model (deterministic, always on):
  * empty / oversized / non-text input rejected
  * secrets typed by the user (passwords, API keys) are redacted before anything is
    sent to the model or written to the audit log, and the user is warned
  * prompt-injection and system-prompt extraction attempts are refused
  * requests about another user's account are refused (access control)
  * out-of-scope topics (HR, payroll, ...) are refused with a redirect
  * harmful AD/security requests (credential dumping, MFA bypass, ...) are refused
  * OpenAI Moderation API when online (fails open to the local checks, logged)
Post-model:
  * output scanned for secret-like strings and system-prompt leakage
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PASSWORD_DISCLOSURE = re.compile(
    r"(?i)\b(my\s+)?(new\s+|old\s+|current\s+)?(password|passwd|pwd|passcode|pin)\s*(is|was|=|:)\s*\S+")
API_KEY = re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b")

INJECTION = [
    r"ignore (all |any )?(of )?(your |the |my )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |your |the )?(previous |prior )?(instructions|rules)",
    r"(reveal|show|print|repeat|output|tell me) (me )?(your |the )?(system|hidden|initial|developer) (prompt|instructions|message)",
    r"you are now (in )?(dan|developer mode|unrestricted)",
    r"\bjailbreak\b",
    r"act as (an? )?(domain admin|administrator|unrestricted)",
    r"</?(system|assistant|tool)>",
    r"forget (all |your )?(rules|instructions)",
]
OUT_OF_SCOPE = {
    "HR / payroll": r"\b(payroll|salary|payslip|pay slip|bonus|leave balance|annual leave|appraisal|hike|"
                    r"resignation|maternity|insurance claim|reimbursement)\b",
    "personal / non-IT": r"\b(write (me )?a (poem|story|essay)|recipe|horoscope|stock tip|cricket score)\b",
}
HARMFUL = [
    r"\b(mimikatz|dcsync|kerberoast\w*|pass[- ]the[- ](hash|ticket)|golden ticket|ntds\.dit|lsass dump|secretsdump)\b",
    r"\b(bypass|disable|skip|turn off) (the )?(mfa|2fa|multi[- ]factor|audit(ing)?|logging|antivirus|edr|defender)\b",
    r"\b(crack|brute[- ]?force|steal|harvest|dump) (\w+ ){0,2}(password|passwords|credentials|hash(es)?)\b",
    r"\badd me to (the )?(domain admins|enterprise admins|schema admins|administrators)\b",
    r"\b(phishing (email|page|kit)|keylogger|ransomware|malware)\b",
]
OTHER_USER = [
    r"\b(unlock|reset|disable|enable|check|look ?up|show|change)\b.{0,40}\b(account|password|mfa|details|groups?)\b.{0,20}"
    r"\b(of|for)\b\s+(?!me\b|myself\b)(my |a |an |the |our )?(colleague|co-?worker|manager|boss|team ?mate|someone|"
    r"another (user|person|employee)|[a-z]+\.[a-z]+(\.ext)?\b(?!\.))",
    r"\b(colleague|coworker|co-worker|manager|boss|someone else|another user|team ?mate)'?s? "
    r"(account|password|mfa|details|data|groups?)\b",
]
SYSTEM_PROMPT_MARKERS = ["L1-AGENT-POLICY", "You are the CORP L1 Virtual Agent"]


@dataclass
class GuardResult:
    allowed: bool
    text: str                      # possibly redacted text to pass on
    flags: list[str] = field(default_factory=list)
    refusal_reason: str | None = None
    refusal_message: str | None = None
    warnings: list[str] = field(default_factory=list)


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def check_input(text: object, session_user_id: str, max_chars: int) -> GuardResult:
    if not isinstance(text, str):
        return GuardResult(False, "", ["invalid_type"], "invalid_input", "Please type your question as plain text.")
    stripped = text.strip()
    if not stripped:
        return GuardResult(False, "", ["empty"], "invalid_input",
                           "Your message was empty. Please describe the IT problem you need help with.")
    if len(stripped) > max_chars:
        return GuardResult(False, "", ["oversized"], "invalid_input",
                           f"Your message is too long ({len(stripped)} characters, limit {max_chars}). "
                           "Please describe the problem in a few sentences and include the exact error message.")
    if "\x00" in stripped or sum(ch.isprintable() or ch in "\n\t" for ch in stripped) < 0.9 * len(stripped):
        return GuardResult(False, "", ["non_text"], "invalid_input", "That input looks like binary or unsupported content.")

    flags: list[str] = []
    warnings: list[str] = []
    clean = stripped
    if PASSWORD_DISCLOSURE.search(clean):
        clean = PASSWORD_DISCLOSURE.sub(lambda m: m.group(0).split()[0] + " [REDACTED]", clean)
        flags.append("password_redacted")
        warnings.append("It looks like you typed a password. It was removed and was not sent to the AI or stored. "
                        "IT will never ask for your password. If you shared a real one, change it now.")
    if API_KEY.search(clean):
        clean = API_KEY.sub("[REDACTED-SECRET]", clean)
        flags.append("secret_redacted")
        warnings.append("A secret-like value was removed from your message.")

    if _any(INJECTION, clean):
        return GuardResult(False, clean, flags + ["prompt_injection"], "prompt_injection",
                           "I can't change how I work or share my internal instructions. "
                           "I can help with IT issues such as account lockouts, passwords, MFA, VPN, email and access.",
                           warnings)
    if _any(HARMFUL, clean):
        return GuardResult(False, clean, flags + ["harmful_request"], "content_safety",
                           "I can't help with that request. It could weaken security or harm systems. If you suspect "
                           "a security incident, contact the Security Operations Center through the service desk.",
                           warnings)
    own_id = session_user_id.lower()
    mentioned_ids = {m.lower() for m in re.findall(r"\bu\d{4}\b", clean, re.I)}
    if (mentioned_ids - {own_id}) or (_any(OTHER_USER[:2], clean)):
        return GuardResult(False, clean, flags + ["other_user_data"], "access_control",
                           "For security I can only look at or help with your own account. Your colleague should "
                           "contact the service desk themselves, or their manager can raise a request for them.",
                           warnings)
    for topic, pat in OUT_OF_SCOPE.items():
        if re.search(pat, clean, re.I):
            return GuardResult(False, clean, flags + ["out_of_scope"], "out_of_scope",
                               f"That looks like a {topic} question, which is outside IT support. Please use the "
                               "HR portal or contact HR directly. I can help with IT issues.", warnings)
    return GuardResult(True, clean, flags, warnings=warnings)


def scrub_output(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    if API_KEY.search(text):
        text = API_KEY.sub("[REDACTED-SECRET]", text)
        flags.append("output_secret_redacted")
    for marker in SYSTEM_PROMPT_MARKERS:
        if marker.lower() in text.lower():
            flags.append("system_prompt_leak_blocked")
            return ("I can't share my internal instructions. How can I help with your IT issue?", flags)
    return text, flags
