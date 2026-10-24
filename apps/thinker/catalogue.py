"""The long documents callers ask for: each type's usual outline, what to ask before writing it, its
formats and size. The scope check (scope.py) picks the type; scoping on the call and the outline the caller approves
start from here; Hermes writes it section by section (apps/control/bigdoc.py).

Short documents (letters, applications, invoices, notices, a resume, a simple table) stay with today's writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

WORDS_PER_PAGE = 400
UNDERSHOOT = 1.25  # the writer reaches ~80% of a section's target : aim 25% higher
WORDS_PER_SLIDE = 70
WORDS_PER_SHEET = 180


@dataclass(frozen=True)
class Kind:
    id: str
    name: str                       # what BolDoc calls it on the call
    doc_type: str                   # DocEngine's doc_type
    formats: Tuple[str, ...]        # unless the caller asks for others
    size: int                       # usual length, in `unit`
    unit: str                       # pages | slides | sheets
    outline: Tuple[str, ...]        # the usual sections (slides, sheets): the caller adds, drops or renames
    asks: Tuple[Tuple[str, str], ...] = ()   # (what, question): asked before writing, unless already said
    research: bool = True           # offer web research (scheme rules, market figures); not for own records


KINDS: Dict[str, Kind] = {k.id: k for k in [
    Kind("project_report", "project report (DPR) for a bank or scheme loan", "report", ("pdf", "docx", "xlsx"), 12,
         "pages",
         ("Executive summary", "Promoter profile", "The business and the project", "Market and demand",
          "Cost of the project and means of finance", "Three-year projections", "Repayment schedule",
          "Risks and how they are handled", "Declaration"),
         (("bank and loan", "Kis bank ya scheme ke liye hai, aur kitna loan chahiye?"),
          ("project cost", "Project ka kharcha kis-kis cheez par hoga, aur aap khud kitna lagayenge?"),
          ("current business", "Abhi business ki monthly sales aur kharcha kitna hai?"))),
    Kind("business_plan", "business plan", "report", ("pdf", "docx"), 15, "pages",
         ("Executive summary", "The problem and the opportunity", "Product or service", "Market and competition",
          "Business model and pricing", "Marketing and sales", "Operations", "Team", "Financial plan",
          "Risks", "Milestones"),
         (("the business", "Business kya hai, aur kahan chalega?"),
          ("customers", "Customers kaun honge, aur aapko kaise milenge?"),
          ("money", "Shuru karne mein kitna paisa lagega, aur kahan se aayega?"))),
    Kind("pitch_deck", "pitch deck", "slides", ("pptx", "pdf"), 12, "slides",
         ("Title", "The problem", "The solution", "Market size", "Product", "Business model", "Traction",
          "Competition", "Go-to-market", "Team", "Financials and the ask", "Thank you"),
         (("audience", "Deck kise dikhana hai: investor, bank, ya client?"),
          ("the ask", "Aap unse kya chahte hain: kitna funding, ya kaun sa deal?"),
          ("traction", "Ab tak kya hua hai: customers, sales, ya koi milestone?"))),
    Kind("internship_report", "internship report", "report", ("pdf", "docx"), 15, "pages",
         ("Acknowledgement", "Abstract", "About the organisation", "Objectives", "Work done", "Tools and methods",
          "Results", "Learning and skills gained", "Challenges", "Conclusion", "References"),
         (("organisation and role", "Internship kis company mein thi, aur aapka role kya tha?"),
          ("work done", "Aapne wahan kya-kya kaam kiya?"),
          ("college and guide", "College ka naam aur guide ka naam kya hai?")), research=False),
    # a project or research report on a subject ("AI par project").
    Kind("topic_report", "project or research report on a topic", "report", ("pdf", "docx"), 12, "pages",
         ("Abstract", "Introduction", "Background", "How it works", "Applications and examples",
          "Benefits and challenges", "Future scope", "Conclusion", "References"),
         (("topic and focus", "Kis vishay par hai, aur kis baat par zyada dhyan dena hai?"),
          ("for whom", "Yeh kiske liye hai: school, college ya office? Koi khaas format ya guideline hai?"))),
    Kind("policy_or_sop", "policy or SOP handbook", "report", ("pdf", "docx"), 12, "pages",
         ("Purpose and scope", "Definitions", "The policy", "Roles and responsibilities", "Procedure",
          "Exceptions", "Compliance and consequences", "Review and updates"),
         (("organisation", "Ye kis organisation ke liye hai, aur kitne log hain?"),
          ("rules", "Is policy mein kaun se main niyam chahiye?"))),
    Kind("grant_proposal", "grant or CSR proposal", "report", ("pdf", "docx"), 12, "pages",
         ("Summary", "About the organisation", "The need", "Objectives", "Activities and timeline",
          "Beneficiaries", "Budget", "Monitoring and evaluation", "Sustainability"),
         (("funder and amount", "Kis funder ya company ko bhej rahe hain, aur kitna grant chahiye?"),
          ("the work", "Project mein kya kaam hoga, aur kinke liye?"))),
    Kind("tender_response", "tender response or quotation", "report", ("pdf", "docx", "xlsx"), 8, "pages",
         ("Cover letter", "Company profile", "Scope of work", "Technical compliance", "Item-wise rates",
          "Terms and conditions", "Delivery schedule", "Declaration"),
         (("tender", "Kaun sa tender hai, aur kis department ka?"),
          ("items and rates", "Kaun se items hain, aur unke rate kya hain?")), research=False),
    Kind("sales_report", "sales or MIS report", "report", ("pdf", "xlsx"), 8, "pages",
         ("Summary", "Sales by product", "Sales by region", "Targets and actuals", "Trends", "Issues",
          "Plan for next month"),
         (("period and figures", "Kis mahine ka report hai, aur sales ke aankde kya hain?"),), research=False),
    Kind("meeting_minutes", "minutes of a long meeting", "minutes", ("pdf", "docx"), 6, "pages",
         ("Meeting details", "Attendees", "Agenda", "Discussion", "Decisions", "Action items", "Next meeting"),
         (("meeting", "Meeting kab hui, kaun-kaun tha, aur agenda kya tha?"),), research=False),
    Kind("lesson_plan", "lesson plan or course material", "report", ("pdf", "docx", "pptx"), 8, "pages",
         ("Learning objectives", "Materials", "Introduction", "Teaching activities", "Practice", "Assessment",
          "Homework"),
         (("class and topic", "Kis class ke liye, aur kaun sa topic?"),)),
    Kind("question_paper", "question paper", "report", ("pdf", "docx"), 6, "pages",
         ("Instructions", "Section A", "Section B", "Section C", "Answer key"),
         (("class, subject, marks", "Kis class aur subject ka, kitne marks ka paper hai?"),)),
    Kind("event_planner", "event or wedding planner workbook", "table", ("xlsx", "pdf"), 5, "sheets",
         ("Budget", "Guest list", "Vendors", "Timeline", "Checklist"),
         (("event", "Kaun sa event hai, kab aur kahan?"),
          ("budget and guests", "Kitna budget hai, aur kitne mehmaan?")), research=False),
    Kind("budget_tracker", "budget or expense tracker", "table", ("xlsx", "pdf"), 3, "sheets",
         ("Monthly budget", "Expenses", "Summary"),
         (("income and costs", "Har mahine ki kamai aur bade kharche kya hain?"),), research=False),
]}

GENERIC = ("Introduction", "Background", "Details", "Analysis", "Recommendations", "Conclusion")


def kind(kind_id: Optional[str]) -> Optional[Kind]:
    return KINDS.get(kind_id or "")


def outline_for(kind_id: Optional[str], sections: Optional[List[str]] = None,
                size: Optional[int] = None) -> Tuple[List[dict], int, str]:
    """(outline, size, unit): section ids s1.., titles (the caller's own when they gave them, else the type's), and
    each section's target length from the document's size."""
    k = kind(kind_id)
    titles = [s.strip() for s in sections or [] if s and s.strip()] or list(k.outline if k else GENERIC)
    unit = k.unit if k else "pages"
    total = int(size or (k.size if k else 10))
    if unit == "slides":  # a section may take several slides: the deck's length is spread over its sections
        per = int(total * WORDS_PER_SLIDE / max(1, len(titles)))
    elif unit == "sheets":
        per = WORDS_PER_SHEET
    else:
        per = int(total * WORDS_PER_PAGE * UNDERSHOOT / max(1, len(titles)))
    return [{"id": f"s{i + 1}", "title": t, "words": per} for i, t in enumerate(titles)], total, unit
