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
# Password-shaped token (letters + digits + symbol, 6+ chars) when the message talks about passwords/sign-in.
# Catches phrasings the explicit pattern misses, e.g. "the one I type is Winter#2025" (live regression case A02).
SECRET_CONTEXT = re.compile(r"(?i)\b(password|passwd|pwd|passcode|pin|log ?in|login|sign ?in|typ(e|ed|ing)|credential)")
PASSWORD_SHAPED = re.compile(r"(?<![\\/\w@.])(?=[^\s]*[A-Za-z])(?=[^\s]*\d)(?=[^\s]*[^A-Za-z0-9\s])[^\s'\"]{6,64}")
DATE_LIKE = re.compile(r"(?i)^(\d{1,2}[-/ ](jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*([-/ ]\d{2,4})?"
                       r"|(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-/ ]\d{1,2}([-/ ]\d{2,4})?"
                       r"|\d{1,2}:\d{2}(am|pm)?|v?\d+(\.\d+)+)$")
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
# Indirect prompt injection: instructions addressed to the AI hidden inside quoted content (emails, tickets).
EMBEDDED_INSTRUCTION = re.compile(
    r"(?is)[\"'“‘]?\s*\b(system|admin|administrator|developer|it|security)?\s*(note|message|instruction|instructions|command|directive)s?"
    r"\s*(to|for)\s*(the\s*)?(ai|assistant|agent|bot|model|copilot)\b\s*[:\-]?"
    # Quoted payload: remove up to the closing quote. Unquoted: remove only up to the end of that sentence,
    # so the user's own description that follows (e.g. "My printer is not working.") is kept.
    r"(?:\s*[\"“‘'][^\"'”’\n]{0,300}[\"'”’]|[^\"'”’\n]{0,300}?(?:[.!?](?=[\s\"'”’]|$)|$))[\"'”’]?")


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


def _names_other_person(text: str, other_names: list[str]) -> bool:
    """True if the text names another directory user (full name, or a possessive first name such as "Meera's")."""
    low = text.lower()
    for name in other_names:
        parts = name.lower().split()
        if len(parts) >= 2 and " ".join(parts[:2]) in low:
            return True
        if parts and re.search(rf"\b{re.escape(parts[0])}['’]s\b", low):
            return True
    return False


def check_input(text: object, session_user_id: str, max_chars: int,
                other_names: list[str] | None = None) -> GuardResult:
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
    if SECRET_CONTEXT.search(clean):
        def _mask(m):
            tok = m.group(0)
            if (tok.lower().startswith(("http", "\\\\", "aadsts", "0x")) or "\\" in tok
                    or re.fullmatch(r"[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}", tok.rstrip(".,;:!?)"))):
                return tok
            if DATE_LIKE.match(tok.rstrip(".,;:!?)")):
                return tok
            return "[REDACTED]"
        masked = PASSWORD_SHAPED.sub(_mask, clean)
        if masked != clean:
            clean = masked
            if "password_redacted" not in flags:
                flags.append("password_redacted")
                warnings.append("It looks like you typed a password. It was removed and was not sent to the AI or stored. "
                                "IT will never ask for your password. If you shared a real one, change it now.")
    if API_KEY.search(clean):
        clean = API_KEY.sub("[REDACTED-SECRET]", clean)
        flags.append("secret_redacted")
        warnings.append("A secret-like value was removed from your message.")

    if EMBEDDED_INSTRUCTION.search(clean):
        clean = EMBEDDED_INSTRUCTION.sub(" [embedded instruction removed] ", clean)
        flags.append("embedded_instruction_removed")
        warnings.append("Your message contained instructions addressed to the AI (for example inside a quoted email). "
                        "They were removed; I only act on your own description of the problem.")
        if not re.sub(r"\[embedded instruction removed\]|[\W_]", "", clean):
            return GuardResult(False, clean, flags, "prompt_injection",
                               "I can't act on instructions addressed to the assistant. Please describe your IT problem.",
                               warnings)
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
    if (mentioned_ids - {own_id}) or (_any(OTHER_USER[:2], clean)) or _names_other_person(clean, other_names or []):
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
