from apps.control.core.orchestrator import Orchestrator
from apps.talker.logic.arbiter import Arbiter, Signals
from apps.talker.logic.state_block import build_state_block
from apps.talker.logic.turns import DictationEndpointer, is_backchannel, is_exit_cue, is_question_to_agent
from docengine.model import Document, Paragraph, Run
from protocol.messages import CreateDoc, Event


def ev(type_, seq, task="t1", **payload):
    return Event(session_id="s", epoch=1, seq=seq, type=type_, task_id=task, payload=payload)


def idle(now, **kw):
    return Signals(now=now, last_user_speech_end=now - 5, **kw)


# ------------------------------------------------------------------------------------------ arbiter
def test_arbiter_never_talks_over_the_caller_or_while_dictating():
    a = Arbiter()
    a.offer(ev("task.completed", 1), now=100)
    assert a.decide(Signals(now=100, user_speaking=True)) is None
    assert a.decide(Signals(now=100, bot_speaking=True, last_user_speech_end=90)) is None
    assert a.decide(Signals(now=100, eou_pending=True, last_user_speech_end=90)) is None
    assert a.decide(Signals(now=100, mode="dictate", last_user_speech_end=90)) is None
    assert a.decide(Signals(now=100, last_user_speech_end=99.5)) is None  # < 1.2 s of silence
    assert a.decide(idle(100)).type == "task.completed"


def test_arbiter_priority_and_one_in_flight():
    a = Arbiter()
    a.offer(ev("task.progress", 1, stage="drafting"), now=0)
    a.offer(ev("task.completed", 2, task="t2"), now=0)
    a.offer(ev("task.question", 3, task="t3", text="GST?"), now=0)
    first = a.decide(idle(10))
    assert first.type == "task.question"
    assert a.decide(idle(11)) is None  # one proactive line at a time
    a.mark_spoken(first.event_id)
    # completion is not urgent: waits for the proactive cooldown
    assert a.decide(idle(12)) is None
    assert a.decide(idle(19)).type == "task.completed"


def test_arbiter_coalesces_progress_and_drops_stale_progress():
    a = Arbiter()
    a.offer(ev("task.progress", 1, stage="outline"), now=0)
    a.offer(ev("task.progress", 2, stage="tables"), now=1)
    out = a.decide(idle(2))
    assert out.payload["stage"] == "tables"
    a.mark_spoken(out.event_id)
    a.offer(ev("task.progress", 3, stage="formatting"), now=3)
    assert a.decide(idle(30)) is None  # stale after 20 s: not worth narrating


def test_arbiter_one_open_question_and_interruption_becomes_must_mention():
    a = Arbiter()
    q1, q2 = ev("task.question", 1, text="A?"), ev("task.question", 2, task="t2", text="B?")
    a.offer(q1, 0)
    a.offer(q2, 0)
    assert a.decide(idle(5)).event_id == q1.event_id
    a.mark_spoken(q1.event_id)
    assert a.decide(idle(6)) is None  # q1 still open
    a.question_resolved()
    got = a.decide(idle(7))
    assert got.event_id == q2.event_id
    a.mark_interrupted(got.event_id)  # caller barged in: not heard
    assert [e.event_id for e in a.must_mention()] == [q2.event_id]
    assert a.decide(idle(8)) is None  # not re-voiced proactively; folded into the next reply


def test_a_question_the_writer_no_longer_needs_is_never_asked():
    a = Arbiter()
    q1, q2 = ev("task.question", 1, text="A?"), ev("task.question", 2, text="B?")
    a.offer(q1, 0)
    a.mark_spoken(a.decide(idle(5)).event_id)   # q1 asked
    a.offer(q2, 6)
    a.drop_questions()                          # q1 answered and the writer moved on before q2 was voiced
    assert a.decide(idle(7)) is None and a.must_mention() == []
    a.offer(q2, 8)                              # a newly asked question is voiced again
    assert a.decide(idle(9)).event_id == q2.event_id


def test_completion_never_silently_expires():
    a = Arbiter()
    done = ev("task.completed", 1)
    a.offer(done, 0)
    a.decide(Signals(now=1, user_speaking=True))
    assert done in a.must_mention()


def test_backchannels_and_cues():
    assert is_backchannel("haan ji") and is_backchannel("ok") and is_backchannel("accha theek hai")
    assert not is_backchannel("haan but change the date")
    assert is_exit_cue("aur kuch nahi, bas") and is_exit_cue("That's it.") and is_exit_cue("ho gaya")
    assert not is_exit_cue("bas stand ke paas milna")  # 'bas' (bus) mid-sentence is not a cue
    assert is_question_to_agent("kya aap ise formal bana sakte ho?") and is_question_to_agent("how long will it take")


def test_dictation_endpointer_adapts_to_the_caller():
    e = DictationEndpointer()
    assert e.threshold_s == 2.5  # default until we learn the caller's rhythm
    for p in [0.6, 0.8, 1.0, 1.1, 1.2, 1.4, 2.6, 0.9, 1.0, 1.3]:  # a slow, thoughtful speaker
        e.observe_pause(p)
    assert 2.0 <= e.threshold_s <= 4.0 and abs(e.threshold_s - 1.9) > 0  # p90 + 0.5, clamped
    assert e.on_silence(1.3, now=100, last_text="and then") == "ack"
    assert e.on_silence(1.4, now=101, last_text="and then") is None      # ack cooldown
    assert e.on_silence(e.threshold_s, now=102, last_text="and then") == "segment"
    assert e.on_silence(5.0, now=103, last_text="and then") == "ask_more"
    assert e.on_silence(0.3, now=104, last_text="bas itna hi") == "exit"


# --------------------------------------------------------------------------------------- state block
def test_state_block_reflects_only_committed_facts():
    o = Orchestrator(clock=lambda: 1.0)
    o.open_session("s", "pstn", "+919988776655")
    o.verify_recipient("s", "+919988776655")
    r = o.handle(CreateDoc(session_id="s", epoch=1, turn_id="t1", formats=["pdf", "docx"], intent="leave letter"))
    block = build_state_block(o.snapshot("s"), mode="converse")
    assert "still being written" in block and "version -" in block and "••6655" in block
    assert "work in progress" in block and "PDF, Word" in block
    t = o.claim_next("w")
    o.commit(t.id, "w", Document(id="x", title="Leave Application", blocks=[Paragraph(runs=[Run(text="hi")])]),
             ["drafted"])
    done = next(e for e in o.s.events["s"] if e.type == "task.completed")
    block = build_state_block(o.snapshot("s"), mode="converse", must_mention=[done])
    assert '"Leave Application"' in block and "version 1" in block and "work in progress" not in block
    assert "must mention" in block and '"Leave Application" is ready' in block
    assert r.doc_id[-6:] in block
    # delivery facts appear only as they really happen
    dlv = o.auto_deliver(r.doc_id)
    assert o.auto_deliver(r.doc_id) is None  # exactly once
    assert "about to be sent" in build_state_block(o.snapshot("s"), mode="converse")
    o.mark_delivery(dlv, "submitted", provider_ref="uuid-1")
    assert "delivery not confirmed" in build_state_block(o.snapshot("s"), mode="converse")
    o.mark_delivery(dlv, "delivered")
    assert not o.mark_delivery(dlv, "submitted")  # late receipts never move status backwards
    delivered = next(e for e in o.s.events["s"] if e.type == "delivery.delivered")
    block = build_state_block(o.snapshot("s"), mode="converse", announce=delivered)
    assert "delivered (version 1)" in block and "say this now" in block and "ending 6655" in block


# ------------------------------------------------------------------------------------------- claims
def test_promises_of_email_or_whatsapp_delivery_are_caught_but_honest_lines_pass():
    from apps.talker.logic.claims import is_wrong_channel
    for said in ["Aapko email par link mil jayega.",
                 "Ismein lagbhag ek minute lagega. Aapko email par link mil jayega.",
                 "Main aapko email kar dungi.", "Link aapke email pe aa jayega.", "I'll email you the link.",
                 "You'll get it by email.", "WhatsApp par bhej dungi.", "आपको ईमेल पर लिंक मिल जाएगा।",
                 "Document Whatsapp pe aa jayega.", "Bas ek minute, aapko email par link mil jayega."]:
        assert is_wrong_channel(said), said
    for said in ["Link SMS se aa jayega.", "Aapka email saurabh at gmail dot com hai, sahi hai?",
                 "Email par nahi, link SMS par aayega.", "Main sirf SMS par link bhejti hoon, email par nahi.",
                 "Kya letter mein aapka email address bhi daalun?", "Aapko SMS par link mil jayega.",
                 "Mera email likh lijiye.", "I can't send it by email, it comes by SMS."]:
        assert not is_wrong_channel(said), said


def test_completion_claims_are_caught_but_honest_future_and_progress_pass():
    from apps.talker.logic.claims import is_completion_claim
    claims = ["26 से 27 October कर दिया है।", "Document ready है।", "Leave Application updated है, 26 से 27 के लिए।",
              "SMS bhej diya hai.", "Maine date badal di hai.", "I've updated the dates.", "It's done.",
              "Aapka document taiyar hai.", "लिंक भेज दिया है।", "Main abhi bana rahi hoon, ho gaya hai."]
    honest = ["Document अभी बन रहा है, लगभग 15 second में ready हो जाएगा।", "Link SMS से आ जाएगा।",
              "मैं अभी बदल रही हूँ।", "Main likh deti hoon.", "I will update the dates.", "Kya SMS aa gaya?",
              "Ready hone par main bata dungi.", "Theek hai, 26 se 27 October.", "Bataiye, kya change karna hai?"]
    assert [s for s in claims if not is_completion_claim(s)] == []
    assert [s for s in honest if is_completion_claim(s)] == []


def test_sentence_split_keeps_unfinished_remainder():
    from apps.talker.logic.claims import split_sentences
    assert split_sentences("पहला वाक्य। दूसरा वाक्य। तीसरा") == (["पहला वाक्य।", "दूसरा वाक्य।"], "तीसरा")
    assert split_sentences("One. Two? Thr") == (["One.", "Two?"], "Thr")
    assert split_sentences("Rs. 2.5 lakh") == (["Rs."], "2.5 lakh")
    assert split_sentences("") == ([], "")


# ------------------------------------------------------------------------------------ language + turn shape
def test_language_from_script_and_hysteresis():
    from apps.talker.logic.language import LanguageTracker, detect
    assert detect("PDF और Word दोनों चाहिए") == "hi-IN" and detect("I need an invoice") == "en-IN"
    assert detect("எனக்கு ஒரு கடிதம் வேண்டும்") == "ta-IN" and detect("నాకు ఒక లేఖ కావాలి") == "te-IN"
    assert detect("ठीक आहे, मला रजेचा अर्ज पाहिजे") == "mr-IN" and detect("") is None
    start = LanguageTracker()  # every call starts in English, until the caller speaks another
    assert start.family == "en-IN" and "English" in start.prompt_line()
    assert start.observe("नमस्ते") == "hinglish" and start.prompt_line() is None  # any Hindi: Hindi/Hinglish
    picked = LanguageTracker.starting("ta-IN")  # picked on the website: kept until they name another
    assert picked.family == "ta-IN" and picked.observe("I need a leave application please") is None
    lt = LanguageTracker(current="hi-IN", family="hinglish")
    steps = ["haan theek hai", "I need a leave application please", "for twenty fifth to twenty seventh october",
             "हाँ सही है", "எனக்கு ஒரு விடுப்பு கடிதம் வேண்டும்", "ok", "PDF venum", "PDF चाहिए हिंदी में"]
    assert [lt.observe(t) for t in steps] == [None, None, "en-IN", "hinglish", "ta-IN", None, None, "hinglish"]
    assert "Tamil" in (LanguageTracker(current="ta-IN", family="ta-IN").prompt_line() or "")


def test_turn_shape_rules():
    from apps.talker.logic.turns import turn_shape
    assert turn_shape("मेरा नाम सौरभ कुमार है और मैं") == "open"
    assert turn_shape("hotel का खर्चा 4200 per night और खाने के लिए") == "open"
    assert turn_shape("26 से") == "open" and turn_shape("I need it for") == "open"
    assert turn_shape("एक मिनट रुकिए") == "wait" and turn_shape("hold on let me check") == "wait"
    assert turn_shape("हाँ सही है, Excel में बना दीजिए") == "closed"
    assert turn_shape("क्या आप Tamil में भी बना सकती हैं") == "closed"
    assert turn_shape("गोवा trip के expense Excel sheet में flight") is None  # left to the classifier
    assert turn_shape("चार") is None and turn_shape("") is None


def test_an_item_named_with_its_amount_still_to_come_is_unfinished():
    """LiveKit's model called "Goa trip के खर्चे में flight" finished and BolDoc answered before the
    amount; the words decide it, however short."""
    from apps.talker.logic.turns import value_missing
    from apps.talker.turn_judge import by_rules
    for said in ["Goa trip के खर्चे में flight", "गोवा trip के expense Excel sheet में flight", "flight का खर्चा",
                 "मेरा account number है", "loan amount होगा", "interiors ka kharcha", "Total amount", "मेरा नाम"]:
        assert value_missing(said) and by_rules(said, 0.8, "") == "incomplete", said
    for said in ["खर्चा कितना है?", "खर्चे में flight 18500 रुपये", "मेरा नाम सौरभ कुमार है", "policy number nahi pata mujhe",
                 "मुझे Goa trip के खर्चे की Excel sheet बनानी है।", "hotel 3 रात का 4200 per night", "account SBI में है"]:
        assert not value_missing(said), said


def test_asking_not_to_be_called_again_is_heard_in_hinglish_hindi_and_english():
    from apps.talker.logic.turns import wants_no_calls
    for said in ["mujhe call mat karna", "dobara phone mat kijiye", "Please don't call me again", "मुझे कॉल मत करना",
                 "फ़ोन मत कीजिए"]:
        assert wants_no_calls(said), said
    for said in ["haan call kar lijiye", "mat bhoolna, call karna", "abhi nahi, baad mein call kijiye"]:
        assert not wants_no_calls(said), said


def test_leave_it_is_a_no_even_when_it_ends_in_a_yes_word():
    from apps.talker.logic.turns import approves, declines
    for said in ["nahi, rehne dijiye", "rehne do", "call mat kijiye", "mat badliye", "No need, leave it",
                 "रहने दीजिए", "नहीं, मत बदलिए"]:
        assert declines(said) and not approves(said), said
    for said in ["haan kar dijiye", "हाँ जी, कर दीजिए।", "theek hai likh dijiye", "Yes, please do it."]:
        assert approves(said) and not declines(said), said


def test_a_caller_may_ask_for_web_research_or_refuse_it_unasked():
    from apps.talker.logic.turns import asks_for_research
    for said in ["Internet से जानकारी जोड़ दीजिए", "internet se bhi dekh lijiye", "Please add data from the internet",
                 "Google se scheme ki details daal dena"]:
        assert asks_for_research(said) is True, said
    for said in ["internet se kuch mat daalna", "nahi, online se jaankari ki zaroorat nahi"]:
        assert asks_for_research(said) is False, said
    for said in ["Cafe mein internet aur WiFi hoga", "Mera online business hai", "haan likh dijiye"]:
        assert asks_for_research(said) is None, said


def test_a_greeting_alone_is_followed_by_the_request():
    from apps.talker.logic.turns import is_greeting
    assert is_greeting("नमस्ते") and is_greeting("Hello hello") and is_greeting("namaste ji")
    assert not is_greeting("नमस्ते मुझे letter लिखना है") and not is_greeting("ji") and not is_greeting("")


def test_one_misheard_word_never_switches_the_language_and_coming_back_is_immediate():
    """"October இல்ல" switched BolDoc to Tamil, and "What did you say?" didn't bring her back."""
    from apps.talker.logic.language import LanguageTracker, detect
    assert detect("October இல்ல") == "en-IN"                       # mostly Latin, one stray Tamil word
    lt = LanguageTracker(current="hi-IN", family="hinglish")
    assert lt.observe("October இல்ல") is None and lt.family == "hinglish"
    assert lt.observe("எனக்கு கடிதம் வேண்டும்") is None              # three words, once: not yet
    assert lt.observe("அக்டோபர் மாதம் விடுப்பு வேண்டும்") == "ta-IN"  # confirmed by the next turn
    assert lt.observe("Yes yeah, what did you say?") == "hinglish"  # a real English sentence: back at once
    lt2 = LanguageTracker(current="ta-IN", family="ta-IN")
    assert lt2.observe("हिंदी में बोलिए") == "hinglish"                # Hindi: back at once


def test_a_language_the_caller_asks_for_is_kept_and_hindi_in_another_script_is_still_hindi():
    """BolDoc answered in Punjabi and stayed there after "I am Hindi speaker"."""
    from apps.talker.logic.language import LanguageTracker, asked_for
    for said, lang in [("I am Hindi speaker", "hi-IN"), ("Hindi mein baat kijiye", "hi-IN"),
                       ("मुझे हिंदी में बात करनी है", "hi-IN"), ("ਮੈਂ ਹਿੰਦੀ ਬੋਲਦਾ ਹਾਂ", "hi-IN"),
                       ("Punjabi nahi aati, Hindi mein boliye", "hi-IN"), ("please speak in English", "en-IN"),
                       ("हिंदी में बोलिए", "hi-IN"), ("Tamil la pesunga, Tamil language", "ta-IN"),
                       ("ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ", "hi-IN"), ("मैं हिंदी स्पीकर हूँ", "hi-IN")]:
        assert asked_for(said) == lang, said
    for said in ["letter English mein likhna hai", "Hindi mein likh dijiye", "PDF चाहिए हिंदी में",
                 "Hindi nahi", "mujhe leave application chahiye"]:
        assert asked_for(said) is None, said
    lt = LanguageTracker()
    # BolDoc doesn't talk in Punjabi : Gurmukhi is a Hindi speaker's words, like "हां जी"
    assert lt.observe("ਹਾਂ ਜੀ") == "hinglish"
    assert lt.observe("ਮੈਨੂੰ ਇੱਕ ਲੈਟਰ ਲਿਖਣਾ ਹੈ ਬੈਂਕ ਲਈ") is None and lt.family == "hinglish"  # never Punjabi
    assert lt.observe("ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ") is None and "they asked for it" in lt.prompt_line()  # Gurmukhi
    assert lt.observe("ਮੈਨੂੰ ਇੱਕ ਲੈਟਰ ਲਿਖਣਾ ਹੈ ਬੈਂਕ ਲਈ") is None  # chosen: a Gurmukhi transcript doesn't move it
    assert lt.family == "hinglish"
    assert lt.observe("please speak in English") == "en-IN" and "English" in lt.prompt_line()


def test_the_callers_own_name_is_picked_up():
    from apps.talker.logic.names import first_name, spoken_name
    assert spoken_name("मेरा नाम है Saurabh") == "Saurabh"
    assert spoken_name("mera naam saurabh kumar hai, main Acme mein") == "Saurabh Kumar"
    assert spoken_name("मेरा नाम सौरभ कुमार है") == "सौरभ कुमार" and spoken_name("my name is Priya") == "Priya"
    assert spoken_name("main Saurabh bol raha hoon") == "Saurabh"
    assert spoken_name("mera naam kya likhna hai") is None and spoken_name("मेरा नाम") is None
    assert spoken_name("Hmm leave चाहिए") is None and first_name("Saurabh Kumar") == "Saurabh"


def test_a_read_back_in_other_words_is_a_repeat_and_bolDoc_is_pronounced():
    from apps.talker.logic.claims import normalized, repeats, speakable, split_sentences
    earlier = ("Theek hai Saurabh, main likh deti hoon. Toh ek mahine ki vacation leave, foreign trip ke liye, aapke "
               "naam se. ")
    said = [normalized(x) for x in split_sentences(earlier)[0]]
    assert repeats("Saurabh ji, ek mahine ki vacation leave, foreign trip ke liye, aapke naam se HR ko letter.", said)
    assert not repeats("Leave kis date se shuru hogi, aur kab tak?", said)
    assert speakable("Namaste! Main BolDoc hoon.") == "Namaste! Main बोल-डॉक hoon."
    assert speakable("I'm BolDoc.", hindi_voice=False) == "I'm Bol Doc."


def test_boldocs_instructions_are_told_apart_from_what_it_may_say():
    """end_call's answer read out word for word. Measured on 3,172 sentences of 118 stored test
    calls: the 12 it matches are all such echoes (two calls)."""
    from apps.talker.logic.claims import echoes, instruction_sentences
    told = instruction_sentences(
        "The caller still wants something (or it isn't clear): answer what they just said. End the call only when "
        "they want nothing more.")
    assert echoes("The caller still wants something (or it isn't clear): answer what they just said.", told)
    assert echoes("End the call only when they want nothing more.", told)
    assert echoes("the caller still wants something, answer what they just said", told)  # a piece of one, unpunctuated
    asked = instruction_sentences('Not yet: their answer wasn\'t a clear yes. Otherwise ask once more, briefly '
                                  '("Maaf kijiye, theek se sunai nahi diya. Badal doon, haan ya nahi?"). It is NOT '
                                  'ready yet.')
    assert not echoes("Maaf kijiye, theek se sunai nahi diya.", asked)  # what an instruction quotes is to be said
    assert not echoes("It is not ready yet.", asked)                     # short: BolDoc may say it itself
    for reply in ["Saurabh ji, leave application likh di hai. Aapko SMS mein link mil jayega.",
                  "Theek hai, sab likh diya hai. Document ready hai, link aapko SMS par aa jayega.",
                  "I'm writing it now, it takes about fifteen seconds."]:
        assert not echoes(reply, told + asked + instruction_sentences(
            "Tell the caller you are writing it now and about how long it takes.")), reply


def test_the_writers_checklist_reads_as_one_question_at_a_time():
    from apps.talker.logic.state_block import intake_line
    loan = {"document": "a bank loan application", "allowed": True, "facts": [
        {"label": "bank", "question": "Which bank?", "value": "SBI", "required": True},
        {"label": "amount", "question": "How much?", "value": "5 crore", "required": True},
        {"label": "loan purpose", "question": "What is the loan for?", "value": "", "required": True},
        {"label": "branch", "question": "Which branch?", "value": "", "required": True},
        {"label": "co-applicant", "question": "Any co-applicant?", "value": "", "required": False}]}
    line = intake_line(loan)
    assert "Given so far: bank: SBI; amount: 5 crore." in line
    assert "one at a time in this order: loan purpose; branch." in line and 'Ask next: "What is the loan for?"' in line
    assert "asks you to write it anyway, write it without it" in line             # a checklist never stalls a call
    assert "co-applicant" not in line                                            # nice-to-haves aren't pressed for
    done = dict(loan, facts=[f for f in loan["facts"] if f["value"]])
    assert "Every needed fact is given" in intake_line(done)
    # never spoken mid-conversation; applied when writing
    assert intake_line({"document": "medical certificate", "allowed": False, "refusal": "from a doctor"}) is None
    assert intake_line({"document": "", "allowed": True, "facts": []}) is None    # nothing asked for yet


def test_writing_and_changing_need_the_callers_yes_to_what_was_read_back():
    """"…aapke naam se. Likh doon?" was answered "is the name sir" and the letter was written."""
    from apps.talker.logic.turns import approved, approves, asked_to_change, asked_to_write, turn_shape
    assert asked_to_write("Toh: LIC agent ko loan application, aapke naam se. Likh doon?")
    # an offer of another document reads nothing back
    offer = "Leave Application ready hai, Saurabh ji. Kuch aur change karna hai ya koi aur document likh doon?"
    assert not asked_to_write(offer) and not asked_to_change(offer) and approved(offer, "haan, likh dijiye") is None
    assert approved("Toh 25 ki jagah 26 October kar doon?", "हाँ कर दीजिए।") == "change"
    assert asked_to_change("Toh: 25 se 27 October ki chhutti. Likh doon?")  # a read-back, also for an edit's yes
    assert not asked_to_change("Toh: 25 se 27 October ki chhutti. Likh doon?", also_write=False)  # not a change
    assert asked_to_write("So: a leave application for 25 to 27 October. Shall I write it?")
    assert not asked_to_write("Main likh rahi hoon.") and not asked_to_write("Kitne amount ka loan chahiye?")
    assert asked_to_change("Toh 25 ki jagah 26 October kar doon?") and not asked_to_change("Kuch badalna hai")
    for yes in ["haan", "haan likh dijiye", "theek hai", "yes go ahead", "ji", "हाँ लिख दीजिए", "Yes, write it down.",
                "ji haan, sab sahi hai", "OK, please go ahead"]:
        assert approves(yes), yes
    for not_yes in ["is the name sir", "What name are you writing under?", "nahi", "ek minute",
                    "haan par amount galat hai", "kitna time lagega", "", "Saurabh"]:
        assert not approves(not_yes), not_yes
    # a yes that goes on to say something is the yes-check's, by meaning
    for more in ["Yes, and the company name is Livo, T L I V O.", "sahi hai, PDF mein", "haan, 26 se 27 kar do"]:
        assert not approves(more), more
    assert turn_shape("मुझे") == "open" and turn_shape("mujhe") == "open"  # a sentence's opener said alone


def test_plivo_says_what_has_played_and_a_cleared_reply_is_never_counted_heard():
    import asyncio
    import time

    from apps.talker.logic.playout import Playout
    from apps.talker.trace import CallTrace
    trace = CallTrace("t")
    po = Playout(trace)
    greeting, reply = po.name(), po.name()
    assert (greeting, reply) == ("r1", "r2")
    po.sent(greeting)
    po.on_event({"event": "playedStream", "sequenceNumber": 7, "streamId": "s", "name": "r1"})
    po.sent(reply)
    po.cut()  # the caller cut in before Plivo said r2 had played: clearAudio went out
    po.on_event({"event": "clearedAudio", "sequenceNumber": 9, "streamId": "s"})
    po.on_event({"event": "playedStream", "streamId": "s", "name": "r2"})  # if Plivo answers it anyway: not heard
    po.on_event({"event": "playedStream", "streamId": "s", "name": "r9"})  # never sent: ignored
    assert [e["e"] for e in trace.events] == ["played", "audio_cleared", "played_after_clear"]
    assert po.confirmed == 1 and all(e.get("ms", 0) >= 0 for e in trace.events)

    async def heard():
        quiet = Playout(CallTrace("u"))  # a line that confirms nothing: the estimate
        assert await quiet.heard_since(time.monotonic(), estimate_s=0.01) == "estimate"
        since = time.monotonic()  # the goodbye starts playing; its checkpoint goes out behind it
        bye = po.name()
        po.sent(bye)
        asyncio.get_running_loop().call_later(0.05, po.on_event, {"event": "playedStream", "name": bye})
        assert await po.heard_since(since, wait_s=1.0) == "played"
        assert await po.heard_since(time.monotonic(), wait_s=0.05) == "timeout"  # nothing sent after it
    asyncio.run(heard())


def test_any_way_of_asking_for_a_language_is_heard_but_not_the_documents_language_or_a_no():
    """How they ask is theirs; a language named for the document, or turned down, is not a request; anything unclear is
    left to the talker model (set_language)."""
    from apps.talker.logic.language import asked_about_document_language, asked_for
    for said, lang in [("Hindi please", "hi-IN"), ("In Hindi", "hi-IN"), ("Hindi mein", "hi-IN"),
                       ("Hindi mein kijiye", "hi-IN"), ("Can we continue in Hindi?", "hi-IN"),
                       ("Switch to Hindi please", "hi-IN"), ("हिंदी में", "hi-IN"), ("Hindi", "hi-IN"),
                       ("Hindi chalega", "hi-IN"), ("Hindi me samjhaiye", "hi-IN"), ("I prefer English", "en-IN"),
                       ("English please", "en-IN"), ("Kya aap Hindi mein baat kar sakti hain?", "hi-IN"),
                       ("I can't speak English, Hindi please", "hi-IN"), ("Hindi please, not English", "hi-IN"),
                       ("English nahi aati, Hindi mein boliye", "hi-IN"),
                       ("Hindi nahi, English mein baat karo", "en-IN"),
                       ("my English is not good, speak in Hindi", "hi-IN"),
                       ("Tamil la pesunga, Tamil language", "ta-IN"),
                       ("I don't understand English well so Hindi", "hi-IN"),
                       ("Can we do the rest of this call in Telugu?", "te-IN"), ("Baaki call Hindi mein", "hi-IN")]:
        assert asked_for(said) == lang, said
    doc_question = "English mein likhun ya Hindi mein?"
    assert asked_about_document_language(doc_question)
    assert asked_about_document_language("Which language should I write the letter in?")
    assert not asked_about_document_language("What would you like to write today: an application, a letter, or a bill?")
    for said, bot in [("Hindi mein", doc_question), ("Hindi", "Which language should I write it in?"),
                      ("Hindi please", "Letter Hindi mein likhun ya English mein?"), ("Hindi nahi", ""),
                      ("I don't speak Hindi", ""), ("My son has a Hindi exam on Monday", ""),
                      ("Hindi mein mat bolo", ""),
                      ("Hindi essay chahiye", ""), ("No Hindi", ""), ("mujhe leave application chahiye", "")]:
        assert asked_for(said, bot) is None, (said, bot)
    # after the welcome's question about writing, a bare language is how they want to talk
    assert asked_for("Hindi please", "What would you like to write today: an application, a letter, or a bill?") == \
        "hi-IN"


def test_a_call_starts_in_the_language_their_last_call_ended_in_and_follows_them_from_there():
    from apps.talker.logic.language import LanguageTracker
    hindi = LanguageTracker.starting(None, "hi-IN")
    assert hindi.family == "hinglish" and hindi.chosen is None and "last call" in hindi.prompt_line()
    assert hindi.observe("Nahi") is None and hindi.call_language() is None   # one word: nothing learnt, kept
    assert hindi.observe("I need a leave application please") is None          # English once: still Hinglish...
    assert hindi.observe("from the twenty fifth to the twenty seventh") == "en-IN"  # ...twice: English, as on any call
    assert hindi.prompt_line().startswith("caller's language: English") and hindi.call_language() == "en-IN"
    picked = LanguageTracker.starting("en-IN", "hi-IN")        # English picked on the website for this call: it wins
    assert picked.family == "en-IN" and not picked.remembered
    tamil = LanguageTracker.starting(None, "ta-IN")            # a remembered language never traps them
    assert tamil.family == "ta-IN" and tamil.observe("मुझे letter चाहिए") == "hinglish"
    quiet = LanguageTracker.starting(None, "hi-IN")
    assert quiet.call_language() is None                        # they said nothing: their language stays as it was
    assert LanguageTracker.starting(None, "xx-IN").family == "en-IN" and LanguageTracker().call_language() is None
    chose = LanguageTracker()
    assert chose.choose("hi-IN") == "hinglish" and chose.chosen == "hi-IN" and chose.call_language() == "hi-IN"
    assert chose.choose("hi-IN") is None                        # already: no switch to make


def test_one_hindi_word_in_an_english_sentence_keeps_the_call_in_english():
    """"Write me a leave application" came as "Right में leave application" (English "me" heard as Hindi "में"), and the
    call went Hinglish on that one word: the Hindi voice and "Saurabh ji"."""
    from apps.talker.logic.language import LanguageTracker, mostly_hindi
    assert not mostly_hindi("Right में leave application")
    assert mostly_hindi("मुझे leave application चाहिए") and mostly_hindi("नमस्ते") and mostly_hindi("PDF चाहिए")
    lt = LanguageTracker()
    assert lt.observe("Right में leave application") is None and lt.family == "en-IN"
    assert lt.observe("मुझे leave application चाहिए") == "hinglish"  # real Hindi still switches at once


def test_the_writers_check_gives_the_callers_own_name_and_the_checklist_says_it_can_lag():
    """Names given any way ("Aapka naam?" "Saurabh") come from the writer's check, by meaning, not from word lists; and
    its checklist says plainly that it can be behind the caller's last words."""
    from apps.talker.logic.state_block import intake_line
    from apps.thinker.intake import _parse
    it = _parse({"document": "leave application", "allowed": True, "caller_name": "Saurabh Kumar",
                 "facts": [{"label": "dates", "question": "Kab se kab tak?", "value": "", "required": True}]})
    assert it.caller_name == "Saurabh Kumar" and it.as_dict()["caller_name"] == "Saurabh Kumar"
    assert _parse({"document": "", "allowed": True, "caller_name": "1234"}).caller_name == ""
    line = intake_line(it.as_dict())
    assert "can lag" in line and "different document" in line and "Kab se kab tak?" in line


def test_the_change_check_is_steadied_by_worked_examples():
    """A sense gate : "नहीं" and "Bus" answering "sab kuch sahi hai ya kuch badalna hai?" were taken for a change 1 time
    in 3, and BolDoc's replies were dropped as ignoring it. The check gets worked examples, as the other judges do:
    an answer that names nothing to change is not a change."""
    import asyncio

    from apps.talker.turn_judge import CHANGE_EXAMPLES, asks_for_change

    class Client:
        def __init__(self, word):
            self.word, self.body = word, None

        async def post(self, url, json=None, headers=None, timeout=None):
            self.body = json
            word = self.word

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": word}}]}
            return R()

    for word, want in [("no", False), ("change", True), ("Change.", True)]:
        c = Client(word)
        assert asyncio.run(asks_for_change(c, "key", "Sab sahi hai ya kuch badalna hai?", "नहीं")) is want
    msgs = c.body["messages"]
    assert msgs[0]["role"] == "system" and len(msgs) == 2 + 2 * len(CHANGE_EXAMPLES)
    assert [m["content"] for m in msgs[2:-1:2]] == [a for _, a in CHANGE_EXAMPLES]
    assert {a for _, a in CHANGE_EXAMPLES} == {"change", "no"}
    assert msgs[-1]["content"].endswith('Caller\'s words: "नहीं"\nAnswer:')



def test_a_no_with_a_change_word_is_the_change_checks_to_judge():
    """"ਨਹੀਂ add ਕਰਨਾ" (nothing to add) was taken for a change request by the word rule, and the claim guard dropped
    every reply after it."""
    from apps.talker.logic.claims import is_change_request
    for no in ["ਨਹੀਂ add ਕਰਨਾ", "nahi, kuch add nahi karna", "नहीं, कुछ नहीं बदलना", "don't change anything",
               "mat badliye"]:
        assert not is_change_request(no), no
    for change in ["address add kar do", "date 26 kar dijiye", "badal do 27 se"]:
        assert is_change_request(change), change


def test_hindi_written_in_another_script_is_hindi_and_punjabi_is_never_spoken():
    """Saaras wrote a Hindi speaker's "ਨਹੀਂ add ਕਰਨਾ" in Gurmukhi, the call went Punjabi, and the next call started in
    it."""
    from apps.talker.logic.language import LanguageTracker, asked_for
    t = LanguageTracker.starting(None, remembered="hi-IN")
    for said in ["ਨਹੀਂ add ਕਰਨਾ", "ਨਹੀਂ add ਕਰਨਾ।", "ਲਿਖ ਦਿਓ ਜੀ ਹਾਂ", "ਮੈਨੂੰ ਇੱਕ ਚਿੱਠੀ ਚਾਹੀਦੀ ਹੈ ਜੀ"]:
        t.observe(said)
    assert t.family == "hinglish"                                  # never Punjabi
    assert LanguageTracker.starting(None, remembered="pa-IN").family == "hinglish"  # a call that drifted into it
    assert LanguageTracker.starting("pa-IN").family == "hinglish"
    assert asked_for("Punjabi mein baat kijiye") is None           # the model says it talks in Hindi or English
    b = LanguageTracker.starting(None, remembered="hi-IN")
    b.observe("নহীং কুছ নহীং চাহিয়ে, বস ঠীক হৈ")                      # Hindi in Bengali letters
    assert b.family == "hinglish"
    real = LanguageTracker.starting(None, remembered="hi-IN")
    for said in ["আমার একটা ছুটির দরখাস্ত লাগবে কাল থেকে", "আমি অফিসে যেতে পারব না তিন দিন"]:
        real.observe(said)                                          # a Bengali speaker still gets Bengali
    assert real.family == "bn-IN"
