#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deep prompt quality testing for Waffler.
Generates 300 synthetic transcripts, tests them, scores them, improves prompts.
"""

import json
import time
import random
import os
from openai import OpenAI

API_KEY = os.environ.get("OPENAI_API_KEY")
PROJECT_PATH = "/Users/tars/Desktop/waffler"
PROMPTS_PATH = os.path.join(PROJECT_PATH, "prompts")

client = OpenAI(api_key=API_KEY)

# ─────────────────────────────────────────────────────────────────
# PROMPT LOADING
# ─────────────────────────────────────────────────────────────────

def load_prompt(filename):
    with open(os.path.join(PROMPTS_PATH, filename), "r") as f:
        return f.read()

# ─────────────────────────────────────────────────────────────────
# TRANSCRIPT GENERATION (300 total, hardcoded for determinism)
# ─────────────────────────────────────────────────────────────────

NORMAL_TRANSCRIPTS = [
    # ── Shopping/todo lists (20) ──
    {"id": "N001", "type": "shopping_list_short", "text": "Milk, eggs, bread, butter."},
    {"id": "N002", "type": "shopping_list_short", "text": "Um, I need, uh, bananas, cereal, and yogurt. That's it."},
    {"id": "N003", "type": "shopping_list_medium", "text": "Okay so the shopping list for this week: chicken breast, broccoli, um, olive oil, pasta, tinned tomatoes, garlic, um, onions, and I think we need washing up liquid too."},
    {"id": "N004", "type": "shopping_list_medium", "text": "Right, grocery run. I need, uh, apples, pears, some strawberries, Greek yogurt, granola, almond milk, and, uh, like, coffee pods — the Nespresso ones, not the, uh, not the cheap ones."},
    {"id": "N005", "type": "shopping_list_long", "text": "Big shop today. So I need: steak, salmon, chicken thighs, bacon, eggs — two dozen — butter, double cream, cheddar, brie, spinach, kale, broccoli, carrots, sweet potatoes, tomatoes, cucumber, red onion, garlic, ginger, lemons, limes, olive oil, coconut oil, soy sauce, fish sauce, quinoa, brown rice, lentils, chickpeas, pesto, pasta, bread — sourdough, not sliced — milk, yogurt, protein powder, and dark chocolate. I think that's everything."},
    {"id": "N006", "type": "shopping_list_long", "text": "Uh okay so for the party this weekend I need: twelve beers — actually make it eighteen — wine, red and white, maybe prosecco too, um, crisps like three bags, nuts, olives, bread and dips, um cheese board stuff — brie, cheddar, some kind of blue, grapes, crackers — sausage rolls, mini quiches, yeah, and napkins, plates, and plastic cups. Oh and candles."},
    {"id": "N007", "type": "shopping_list_short", "text": "Three things: coffee, milk, paracetamol."},
    {"id": "N008", "type": "shopping_list_medium", "text": "For dinner tonight I want to make a curry so I need: um, chicken, onions, garlic, ginger, tinned tomatoes, coconut milk, garam masala, turmeric, cumin, coriander, and, um, rice. And naan if they have it."},
    {"id": "N009", "type": "shopping_list_medium", "text": "Gym stuff to order: new lifting belt — actually no the old one is fine — um, pre-workout, creatine, protein bars, and new headphones. The wireless ones, not wired."},
    {"id": "N010", "type": "shopping_list_short", "text": "Toiletries: toothpaste, shampoo, deodorant, razors."},
    {"id": "N011", "type": "shopping_list_medium", "text": "Amazon order: HDMI cable, um, a desk lamp — the one with USB charging built in — some cable ties, and, uh, a wireless mouse. Black one."},
    {"id": "N012", "type": "shopping_list_long", "text": "Home office supplies I need to order: printer paper, um, A4, two reams, black printer ink cartridges, um, the HP ones — 305 I think — sticky notes, um yellow and pink, highlighters, a new stapler, staples, paper clips, binder clips, a hole punch — oh and some folders, the lever arch ones — and a pen organiser for my desk. And maybe a whiteboard. Yeah, a small whiteboard."},
    {"id": "N013", "type": "shopping_list_short", "text": "Dinner stuff: spaghetti, mince, jar of passata."},
    {"id": "N014", "type": "shopping_list_medium", "text": "Okay plants I want to buy: a monstera, um, or actually maybe a fiddle leaf fig — no, monstera, easier to look after — a snake plant, some succulents, and a pot that's not too expensive."},
    {"id": "N015", "type": "shopping_list_medium", "text": "Right book order: Atomic Habits — actually I already have that — um, Deep Work by Cal Newport, The Psychology of Money, and, uh, Thinking Fast and Slow. Four Wait no three books."},
    {"id": "N016", "type": "shopping_list_short", "text": "From the chemist: vitamin D, omega 3, zinc."},
    {"id": "N017", "type": "shopping_list_medium", "text": "Birthday stuff for mum: card, flowers — she likes peonies — um, chocolate, the nice hotel chocolat ones, and maybe a candle. Like a fancy one. Jo Malone or something."},
    {"id": "N018", "type": "shopping_list_long", "text": "Camping trip checklist: tent, sleeping bags — two, um, the three season ones — roll mats, um, headtorches, spare batteries, camping stove, gas canisters, pots and pan, utensils, plates, cups, um, food — pasta, instant noodles, cereal bars, coffee, um, dried fruit, nuts — first aid kit, insect repellent, suncream, waterproofs, um, warm layers, hiking boots, spare socks — loads of spare socks — water bottles, um, a water filter, a map, and don't forget matches and firelighters."},
    {"id": "N019", "type": "shopping_list_short", "text": "Just milk and bread, that's it."},
    {"id": "N020", "type": "shopping_list_medium", "text": "Tech accessories: USB-C hub, um, screen cleaner, a laptop stand — the adjustable one — and a Bluetooth keyboard. Apple one if it's not too pricey."},

    # ── Quick reminders and notes (15) ──
    {"id": "N021", "type": "reminder", "text": "Remind me to call the dentist tomorrow morning."},
    {"id": "N022", "type": "reminder", "text": "Um, note to self: the meeting with Sarah is moved to Thursday, not Wednesday. Thursday at two."},
    {"id": "N023", "type": "reminder", "text": "Don't forget to send the invoice to the client before end of day Friday."},
    {"id": "N024", "type": "reminder", "text": "So, like, I need to remember to take the car in for a service next week. Book it for Monday or Tuesday."},
    {"id": "N025", "type": "reminder", "text": "Quick note: the password for the staging server is in the shared vault under 'staging-prod'. Tell the team."},
    {"id": "N026", "type": "reminder", "text": "Um, reminder: pick up the dry cleaning today on the way home. Don't forget this time."},
    {"id": "N027", "type": "reminder", "text": "Note: cancel the Netflix subscription before the 25th or they'll charge again."},
    {"id": "N028", "type": "reminder", "text": "Okay so I need to remember, uh, to review James's pull request tonight. It's been sitting there two days."},
    {"id": "N029", "type": "reminder", "text": "Reminder to self: water the plants before leaving for the trip on Thursday."},
    {"id": "N030", "type": "reminder", "text": "Um, don't forget: the kids' school play is Tuesday evening, seven PM. Book the restaurant after."},
    {"id": "N031", "type": "note", "text": "Just a note: the new API rate limit is 1000 requests per minute, not per hour. Update the docs."},
    {"id": "N032", "type": "note", "text": "Quick thought: we should add a dark mode option to the app. Users keep asking."},
    {"id": "N033", "type": "note", "text": "Um, note: the gym closes early on Sundays — five PM, not nine. Go in the morning."},
    {"id": "N034", "type": "note", "text": "So I just thought of something — we need to set up automatic backups before the launch. Critical."},
    {"id": "N035", "type": "note", "text": "Note to self: try intermittent fasting for two weeks and track energy levels and weight."},

    # ── Short messages to people (15) ──
    {"id": "N036", "type": "message", "text": "Text to Tom: hey, are you still on for football Sunday? Let me know by Friday so I can sort the teams."},
    {"id": "N037", "type": "message", "text": "Message to the team: just a heads up, the standup tomorrow is cancelled. We'll sync on Thursday instead."},
    {"id": "N038", "type": "message", "text": "Email to landlord: hi, um, this is James from flat three. The boiler is making a strange noise again — a kind of banging sound. Could someone take a look this week? Thanks."},
    {"id": "N039", "type": "message", "text": "WhatsApp to mum: I'll be there around six, not five. Traffic's bad. Can you hold dinner? Love you."},
    {"id": "N040", "type": "message", "text": "Reply to Sophie's message: that sounds amazing, I'm in. What time and where should I meet you?"},
    {"id": "N041", "type": "message", "text": "Message to my manager: quick heads up, I'm going to be late this morning — about half an hour. Sorry, train issues."},
    {"id": "N042", "type": "message", "text": "Text to the group chat: who's coming to the pub quiz tonight? Need to know numbers."},
    {"id": "N043", "type": "message", "text": "Email to support: hello, I purchased a subscription last month but I've been charged twice. Can you please check and refund the duplicate? My order number is 884721. Thanks."},
    {"id": "N044", "type": "message", "text": "Message to Dan: um, I've finished the designs for the landing page. I've sent them to your email — let me know your thoughts and if you want any changes."},
    {"id": "N045", "type": "message", "text": "Quick message to Sarah: the report is done. I've left it on your desk. A few things to double-check on page four, I've highlighted them."},
    {"id": "N046", "type": "message", "text": "Text to dad: happy birthday! Sorry I can't be there in person. I'll call you tonight. Have a great day."},
    {"id": "N047", "type": "message", "text": "Email to client: just following up on the proposal I sent last week. Would love to get your thoughts. Happy to jump on a call if easier."},
    {"id": "N048", "type": "message", "text": "Message to flatmate: hey, um, can you make sure the bins go out tonight? Collection is early tomorrow and I forgot last week. Cheers."},
    {"id": "N049", "type": "message", "text": "Slack message to devs: heads up — don't deploy to production tonight. I'm running a DB migration first thing tomorrow and we need a clean state."},
    {"id": "N050", "type": "message", "text": "Text to Jen: loved seeing you last night. Let's do it again soon — maybe that Italian place next time? Let me know when you're free."},

    # ── Tasks and instructions (15) ──
    {"id": "N051", "type": "task", "text": "Create a new spreadsheet tracking monthly expenses with columns for date, category, amount, and notes."},
    {"id": "N052", "type": "task", "text": "Um, so I need someone to, uh, review the current onboarding flow and write a report on where users drop off."},
    {"id": "N053", "type": "task", "text": "Set up a recurring calendar event every Monday at nine AM for the weekly all-hands."},
    {"id": "N054", "type": "task", "text": "Can you, uh, draft a job description for a senior front-end developer role? React, TypeScript, four plus years experience, remote, competitive salary."},
    {"id": "N055", "type": "task", "text": "I need a summary of all the tickets closed in the last sprint. Group them by type: bugs, features, tech debt."},
    {"id": "N056", "type": "task", "text": "Write a short bio for my LinkedIn — about three sentences. I'm a product designer with eight years experience, I've worked at startups and large tech companies, focus on user-centred design."},
    {"id": "N057", "type": "task", "text": "Uh, set the meeting agenda for Friday: first item is the Q1 review, then the roadmap discussion, then AOB. Forty-five minutes total."},
    {"id": "N058", "type": "task", "text": "Update the project README with the new installation steps. The old ones are outdated and people keep getting confused."},
    {"id": "N059", "type": "task", "text": "Schedule a catch-up with the new intern — um, twenty minutes, any time Thursday afternoon is fine."},
    {"id": "N060", "type": "task", "text": "I need a three-slide deck: slide one is the problem, slide two is our solution, slide three is the ask. Keep it clean and minimal."},
    {"id": "N061", "type": "task", "text": "Sort out the broken link on the website — the 'Contact Us' button in the footer goes to a 404. Fix it and push."},
    {"id": "N062", "type": "task", "text": "Can you compile all the customer feedback from this month into a theme list? Just the top themes, not every individual comment."},
    {"id": "N063", "type": "task", "text": "Um, send the weekly digest to the newsletter list — use the same template as last week, just update the date and the featured article."},
    {"id": "N064", "type": "task", "text": "Okay so I need to prepare for the investor meeting next Tuesday. Pull together the key metrics: MRR, churn, CAC, LTV, and the growth chart."},
    {"id": "N065", "type": "task", "text": "Archive all the files from the 2024 projects folder and move them to cold storage."},

    # ── Noise/garbage/testing inputs (10) ──
    {"id": "N066", "type": "noise", "text": "testing testing one two three"},
    {"id": "N067", "type": "noise", "text": "um... uh... um..."},
    {"id": "N068", "type": "noise", "text": "is this thing on"},
    {"id": "N069", "type": "noise", "text": "hello? hello? can you hear me?"},
    {"id": "N070", "type": "noise", "text": "okay um right so um yeah um"},
    {"id": "N071", "type": "noise", "text": "test test"},
    {"id": "N072", "type": "noise", "text": "la la la la la la"},
    {"id": "N073", "type": "noise", "text": "one two three four five six seven"},
    {"id": "N074", "type": "noise", "text": ""},
    {"id": "N075", "type": "noise", "text": "asdfghjkl"},

    # ── Mixed content (10) ──
    {"id": "N076", "type": "mixed", "text": "So I was thinking about the weekend, you know, maybe going to the beach, but actually — wait, more importantly — remind me to send the contracts to the solicitor by Thursday. That's the critical thing."},
    {"id": "N077", "type": "mixed", "text": "Um, hey, text to Jake, like, happy birthday mate — actually no wait, before that, add 'call solicitor' to my task list, that's urgent. Then the message to Jake: happy birthday, hope you're having a great day."},
    {"id": "N078", "type": "mixed", "text": "Okay so groceries: apples, milk, eggs. Also remind me, gym at six. And actually send a message to the team about the deadline moving to next Friday."},
    {"id": "N079", "type": "mixed", "text": "Right so I started thinking this was just a note but actually it's turning into a task: someone needs to audit the user permissions in the admin panel before launch. Make that a ticket."},
    {"id": "N080", "type": "mixed", "text": "I wanted to write an email to Claire but instead let me just note: the Q3 numbers are looking strong, up fourteen percent, and we should highlight that in the board meeting."},
    {"id": "N081", "type": "mixed", "text": "Um, ingredients for tonight: pasta, garlic, chilli. Oh and actually while I'm at it — book a table somewhere for Saturday, somewhere nice, not a chain."},
    {"id": "N082", "type": "mixed", "text": "First thought was a message to my landlord about the heating, but more urgently: I need to back up my laptop before the software update tonight."},
    {"id": "N083", "type": "mixed", "text": "Note to self about the product: users are complaining about load times. But actually, task for the dev team: profile the API endpoints and fix the slowest three."},
    {"id": "N084", "type": "mixed", "text": "Shopping: wine, cheese, crackers for the party. And, uh, message to everyone: the party is now at eight not seven, sorry for the late notice."},
    {"id": "N085", "type": "mixed", "text": "Started to think about writing my CV, then realised more urgently — cancel that gym membership before they charge on the first."},

    # ── Natural conversation snippets with heavy filler words (15) ──
    {"id": "N086", "type": "filler_heavy", "text": "So basically, like, I was thinking, you know, that we should, um, kind of, like, restructure the nav so that, uh, the settings are, like, easier to find, you know what I mean?"},
    {"id": "N087", "type": "filler_heavy", "text": "Um, so, like, the thing is, right, I sort of, uh, want to, basically, get the app to, like, automatically, you know, sync in the background, like every hour or something."},
    {"id": "N088", "type": "filler_heavy", "text": "Okay so, like, I literally just, um, had this idea, right, and it's, kind of, basically, a, uh, new dashboard that like, shows the, you know, real time data, like, at a glance."},
    {"id": "N089", "type": "filler_heavy", "text": "Yeah so, um, the, you know, the meeting went, like, really well actually, and, uh, they're, sort of, keen to, like, move forward, basically, with the pilot."},
    {"id": "N090", "type": "filler_heavy", "text": "So I, um, kind of, like, need to, uh, basically, talk to, you know, whoever is responsible for, like, the server costs, because, um, they're getting, sort of, out of hand."},
    {"id": "N091", "type": "filler_heavy", "text": "Like, the thing that, um, I keep coming back to, you know, is that, basically, the onboarding is, like, too long, and, uh, people are, sort of, dropping off before they even, like, get started."},
    {"id": "N092", "type": "filler_heavy", "text": "So, like, I was, um, basically going to, you know, say to the client that, uh, we're, like, on track, but, kind of, there are some, um, minor delays, sort of, that might push things back a week."},
    {"id": "N093", "type": "filler_heavy", "text": "Um, yeah, so, like, the design is, you know, basically done, but, uh, there's, like, a few, sort of, edge cases that we, um, haven't, like, fully worked out yet."},
    {"id": "N094", "type": "filler_heavy", "text": "So I, uh, kind of, like, want to, you know, propose that we, basically, um, split the team into two, like, smaller squads to, sort of, move faster."},
    {"id": "N095", "type": "filler_heavy", "text": "Yeah so, like, um, the feedback from users is, you know, basically that, uh, the app is, like, great but, sort of, the, um, notifications are, kind of, too aggressive."},
    {"id": "N096", "type": "filler_heavy", "text": "Okay so, um, I, like, literally need to, you know, figure out, sort of, what's happening with, uh, the API, because it's, basically, been down, like, twice this week."},
    {"id": "N097", "type": "filler_heavy", "text": "So like, um, we should, you know, kind of, look into, uh, whether we can, basically, like, automate the reporting, because, sort of, doing it manually is, um, just taking too long."},
    {"id": "N098", "type": "filler_heavy", "text": "Um, so, like, I'm, uh, thinking, you know, that the, basically, the best approach is to, sort of, start small, like, prototype it first, and then, um, scale if it works."},
    {"id": "N099", "type": "filler_heavy", "text": "Yeah, like, um, the current situation, you know, with the, uh, like, data pipeline is, basically, not, sort of, sustainable, and, like, we need to, um, address it properly."},
    {"id": "N100", "type": "filler_heavy", "text": "So, uh, like, I was thinking, you know, um, maybe, sort of, we should, like, do a proper, basically, retrospective on the launch, um, because there's, like, a lot to, you know, learn from."},
]

RAMBLE_TRANSCRIPTS = [
    # ── Long brain dumps (25) ──
    {"id": "R001", "type": "brain_dump", "text": "Okay so I've been thinking about this all morning and I need to get it out of my head. So basically the app — right, the whole app — needs a rethink on the onboarding. Like we've been getting users in but they're not activating. And the activation is the thing, you know, that's the metric that matters. So I was thinking, what if we do a — okay wait, what if we do like a three-step thing instead of the current seven steps. Right because nobody wants to go through seven screens just to get started. And then the second thing — completely separate — I need to talk to the finance team about the server costs because they've gone up like forty percent this quarter and that's just not sustainable. We're burning money we don't have. And then third thing, and this is more of a longer term thing, I want to explore whether we should build a native app or keep it web-first. Like the web app works fine but users who use it on mobile are having a terrible time. And I know, I know we said we'd revisit this in Q3 but I think we need to pull that forward. Oh and also — completely forgot — the freelancer who's been doing our social media, her contract ends this month, we need to decide if we're renewing or taking it in-house."},
    {"id": "R002", "type": "brain_dump", "text": "So the thing that's been rattling around in my brain today is the pricing model. We're currently on a flat monthly subscription which is fine but I think we're leaving money on the table. Like power users are getting an insane amount of value and paying the same as someone who uses it once a week. So there's this usage-based model idea where you pay per — hmm, per what though, per transaction? per API call? per user seat? I think per seat makes most sense. Okay but then there's the enterprise angle, right, because enterprise clients want predictable billing, they don't want surprises. So maybe a hybrid — base subscription plus usage overage above a threshold. And then separately — completely different topic — I had a thought about the help docs. They are genuinely terrible. Nobody reads them. I feel like we should do video walkthroughs instead. Short ones, like two minutes each, just showing the main features. And then one more thing: the CTO asked about our disaster recovery plan and honestly I don't think we have a proper one. That's a gap. We need to document it and test it. Like actually test it, run a drill."},
    {"id": "R003", "type": "brain_dump", "text": "Right, brain dump time. So number one: I keep thinking about how our competitors are positioning. Like the main competitor just dropped their price by twenty percent, which is aggressive. Do we respond? I don't think we should — racing to the bottom on price is a trap. We should double down on quality and support. Number two: the team culture thing. I've noticed morale has dipped a bit since the last release crunch. I want to do something about it. Maybe a team day out. Maybe just recognise people more publicly. Number three: technical debt. The auth system is seven years old and we've been papering over cracks for too long. It needs a proper rewrite. That's going to take someone a full sprint. Number four: I want to explore AI features. Not because everyone's doing it but because there are genuine use cases in our product — smart suggestions, auto-categorisation, anomaly detection. Five: the redesign of the dashboard that we keep pushing back — it has to happen this quarter, users are complaining about it every week. And finally: the partnership with that payments company, I think we should just move forward, the due diligence is done, stop overthinking it."},
    {"id": "R004", "type": "brain_dump", "text": "Okay morning thought dump. So I woke up thinking about the release schedule. We've been doing monthly releases and they're too stressful, too much pressure, everyone's burning out right before each one. I want to switch to continuous deployment with feature flags. That way stuff can be merged whenever it's ready, flagged off, and turned on when we're confident. Way less drama. Second thing: user research. We haven't done proper user research in like eight months. We're just building on assumptions. We need to schedule at least ten customer interviews this quarter. Third thing: the mobile app. Our Android ratings have dropped to three point two stars and I'm pretty sure it's the notification bug that's been there for six weeks. That bug needs to be priority one this sprint. Fourth: I saw a tweet from a competitor launching a feature we've had on our roadmap for a year. We just need to ship faster. Fifth: the new starters starting in two weeks — three of them — we need the onboarding docs updated because the current ones are for the old tech stack. Sixth: I need to have a difficult conversation with one of the team leads about communication style. Going to prepare for that this week."},
    {"id": "R005", "type": "brain_dump", "text": "So I've got about five thoughts that need to come out. First one is about SEO — we've basically ignored it for the past year and organic traffic has flatlined. I want to bring in a specialist or at least do an audit ourselves using, uh, I don't know, Semrush or Ahrefs. Second is about the checkout flow. I looked at the funnel data yesterday and there's a significant drop-off at the payment step. I think it's the form — too many fields. We should test a streamlined version with just the essentials. Third thought: do we need a Chief of Staff? We've grown to thirty people and I'm spending too much time coordinating instead of thinking. Fourth: the beta group, the users who've been with us from the start, we don't show them enough love. We should create a proper community for them — like a private Slack or something. Fifth — and this is more personal — I need to get better at delegating. I hold on to too much. I should probably think about what I can let go of this quarter. Maybe the vendor relationships. Maybe the weekly finance review. Okay I think that's it."},
    {"id": "R006", "type": "brain_dump", "text": "Right I need to think out loud about the product roadmap for Q2. So we've got three main pillars as far as I can see. Pillar one is retention — we need to reduce churn, which means better onboarding, better in-app education, maybe a loyalty thing. Pillar two is growth — new channels, partnerships, maybe a referral programme, maybe go after a new vertical. Pillar three is platform — foundation work, performance, scalability, the stuff that isn't sexy but matters. And then there's a tension right, because the team wants to build new features but the platform work keeps getting pushed back and that's accumulating risk. And then there's also the question of whether we should launch in Europe this year or focus on deepening the US market first. I lean towards deepening but there's a board member who thinks we should move faster internationally. And then — separate thread — I want to revisit our support model. We're doing fully manual support and it's not scaling. Some kind of AI-assisted triage could cut the workload in half. That's a project someone needs to own."},
    {"id": "R007", "type": "brain_dump", "text": "Okay so I'm walking and thinking and I'll just ramble and organise it later. Thing one: the marketing website is embarrassing. It's four years old, it doesn't reflect who we are now, the copy is vague, the design is dated. We need a new one. I'd say a one-month project if we scope it tightly. Thing two: I had a call with a potential partner earlier — they do logistics software and their users would benefit massively from our product. We should pursue that partnership. They want a white-label option which is something we don't currently support but it's not impossible. Thing three: our data team keeps asking for a proper data warehouse and I keep saying no because of cost but the reporting we're doing out of the production database is getting riskier as we scale. We need to at least start a migration. Thing four: I want to write more publicly — blog posts, LinkedIn, whatever — as a founder it builds trust and we get better inbound. Thing five: we should open source the SDK. It'll drive developer adoption. Competitors are doing it and winning. Thing six: hire a head of marketing, stop doing it ourselves, it's not our skill set."},
    {"id": "R008", "type": "brain_dump", "text": "Morning brain dump, just talking through what's on my mind. So the biggest thing right now is the fundraise. We've had three conversations this week, two went well, one was a waste of time. The pitch is getting sharper but we need better metrics storytelling — the numbers are good but how we present them isn't compelling enough. I want to work on the deck this weekend. Second thing: the team asked me about salary reviews and I've been dodging it because the finances aren't there yet, but I can't keep dodging it — I need to have that honest conversation this week. Third thing: I'm feeling a bit burned out and I wonder if others are too. We've been sprinting for six months. Maybe it's time to give the team a proper break — like a company-wide long weekend or something. Fourth: our biggest customer emailed yesterday with a feature request that, if we built it, would probably help a lot of other customers too. I should prioritise it. Fifth: the security audit from last month flagged three medium issues that we still haven't addressed. Those need to go on the sprint immediately."},
    {"id": "R009", "type": "brain_dump", "text": "So I'm trying to figure out our analytics strategy and it's messy. So right now we're using Google Analytics which is fine for basic stuff but we have no proper event tracking, no product analytics, nothing. So we need to instrument the app properly. I'm thinking Mixpanel or Amplitude. Amplitude seems more powerful but Mixpanel is simpler. We don't need that much power right now so maybe Mixpanel. And then we need to decide what events to track — like every click? No, that's too much. Key actions: account creation, first meaningful action, subscription upgrade, churn. And then on top of that we want to build a proper reporting dashboard for the team so everyone can see the metrics that matter to them without having to ask the data team. That's a separate project. And then there's the privacy angle — we need to make sure whatever we implement is GDPR compliant, we've got European users. And — separate thing entirely — I need to reply to the journalist who emailed asking about us for an article. That's a good PR opportunity. And also: the team standup is consistently overrunning by twenty minutes. I need to fix that."},
    {"id": "R010", "type": "brain_dump", "text": "Right I want to capture some thoughts on the engineering culture. So we're growing the team but I don't feel like we have strong enough processes. Things just kind of happen ad hoc. So I'm thinking: we need better code review culture — meaningful reviews, not just rubber stamping. We need proper on-call rotations, currently it's just whoever is around. We need architecture decision records — like write down why we made key technical decisions so future team members understand. We need a proper incident post-mortem process, right now we just fix things and move on without learning from them. And we need to get better at documentation — the codebase has huge sections with no comments, no README, nothing. Like future us is going to hate current us. And — separate thought — should we do pair programming more? Some people love it, some hate it, I don't want to mandate it but maybe encourage it. And I also want to think about whether our sprint length is right. Two weeks feels a bit long sometimes. Would one week be better? I don't know. Worth an experiment."},
    {"id": "R011", "type": "brain_dump", "text": "So I've been obsessing about the customer success function and I think we're doing it wrong. Or not wrong, but we're leaving a lot of value on the table. So currently it's reactive — customers come to us with problems and we solve them. But what if we flipped it to proactive? Like monitor usage patterns, spot when customers are struggling or underusing features, reach out before they churn. That's the thing — we can usually see churn coming three or four weeks in advance in the usage data, but nobody's acting on those signals. And then there's the expansion revenue angle — like there are customers who could be on a higher plan but nobody's ever had that conversation with them. That's just money sitting on the table. And then on the completely different end of things, I realised we haven't updated the security policy page in two years. Customers are starting to ask more security questions in sales calls. We need better security collateral. Like a security FAQ, an overview doc, ideally a SOC 2 report — which I know takes time but we should start that process. And I need to send my parents those holiday photos from Christmas. I keep forgetting."},
    {"id": "R012", "type": "brain_dump", "text": "Okay so the thing that's been annoying me for weeks is the deployment process. It's too manual. Like we have a checklist document that someone has to go through step by step. That should all be automated. CI/CD pipeline, automated tests, auto deploy to staging on every PR, one click to production. And then there's the monitoring — we get alerted when things are fully broken but we have no visibility into degraded performance. Like our API could be three times slower than normal and we wouldn't know until a customer complained. We need better APM — application performance monitoring. I'm thinking Datadog or New Relic. And then — different topic — the marketing team wants more product screenshots and demo videos and I keep saying yes and never delivering. I should allocate one day this month to just do that. And then — this is actually important — we should think about what our AI strategy is. Like properly. Everyone's adding AI features, but what's the meaningful version for our product? I don't want to just add a chatbot. I want something that genuinely improves the core workflow."},
    {"id": "R013", "type": "brain_dump", "text": "Alright morning thoughts. One: the sales team needs better battle cards. When we go up against Competitor A or B, we don't have a clear narrative about why we win. We need to articulate that and arm the sales team. Two: our NPS is sitting at 34 which is okay but not great. I want to understand the detractors better — what are they unhappy about. Someone should do a call with the last ten detractors. Three: the design system. We started building one but it stalled. The inconsistency across the product is starting to show and it's slowing down development because everyone's making different choices. We need to commit to finishing the design system this quarter. Four: I want to experiment with a product-led growth motion. Free tier, viral loop, upgrade triggers. We've been entirely sales-led but the unit economics would be much better with self-serve. Five: the board presentation is in three weeks. Need to start preparing. Key message should be: strong retention, growth on track, team scaling well. Six: I'm worried about key person risk on the engineering side. Two people know the most critical parts of the system and if either left it would be a serious problem. Need to address through documentation and knowledge sharing."},
    {"id": "R014", "type": "brain_dump", "text": "So I've been thinking about my own productivity and I want to capture this before I forget. The mornings are when I do my best thinking but I keep letting meetings eat into them. I need to block out nine to twelve every day as deep work time and stop being available. The afternoons are fine for meetings and calls. Also I want to have a weekly review practice — like every Friday, fifteen minutes, review what I did, what moved the needle, what I should carry forward. I keep having the same thoughts every week because I don't capture them properly. And then on the personal side — I want to start going to the gym again. I stopped three months ago. Morning sessions, three times a week, non-negotiable. And I want to read more. I've got like ten books half-started and none finished. One book at a time, no new books until I finish the current one. And I want to meditate more consistently. I've been doing it sporadically and that's not enough. Ten minutes every morning, right after waking up, before phone. And I want to spend less time on social media because it's just making me anxious and I'm not getting anything useful out of it. Maybe a screen time limit. Thirty minutes a day max."},
    {"id": "R015", "type": "brain_dump", "text": "Okay so work stuff first. We have a big client meeting on Thursday and I don't think we're prepared. We need to review the account history, know what they've asked for in the past, have answers ready for the obvious questions. Someone needs to own the prep for that. Second: the intern who's been here six weeks — I think she's great and we should make her a full-time offer before she accepts something else. She's already doing the work of a junior developer. Third: I keep getting asked about our long-term vision by investors and I give a slightly different answer every time. I should write it down, nail it, make it consistent. Fourth: the API documentation is out of date by about four releases. Developers are getting frustrated. This needs to be a regular part of our release process, not an afterthought. Fifth: I want to reduce the number of tools we use. We have Slack, Teams, email, Notion, Jira, Confluence, Asana — I don't know why we have all of these. The cognitive overhead is real. Consolidate. Sixth: I should probably see a physio about my shoulder. It's been bothering me for two months."},
    {"id": "R016", "type": "brain_dump", "text": "So thinking about the go-to-market strategy for the new product line. Right now we're going to sell it as an add-on to existing customers first. That makes sense, they already trust us, shorter sales cycle. Then after six months, if the product-market fit is proven, we go broader. But there's a debate internally about whether to price it as a separate SKU or bundle it in. I lean bundle — it drives adoption, reduces friction — but the CFO wants it separate for revenue visibility. I think we can have both: bundle for existing customers as a promo, separate SKU for new customers. And then the naming — we've been going back and forth on this and it's taking too long. The three options are: keep it under the main brand, give it a sub-brand, or give it a completely separate name. I think sub-brand. And the launch timeline is too aggressive. We said six weeks but there's still a lot to do. I'd push it to ten weeks. Better to launch properly. And — separate note — I need to check in with the person who's been on sick leave. Just a friendly check-in, nothing work-related."},
    {"id": "R017", "type": "brain_dump", "text": "Right I want to brainstorm about the community strategy. So we have a Slack community that's pretty dead. Like two hundred members and maybe ten active. And I think the problem is we never made it valuable enough. It was just a support channel basically. What if we actually invested in it? Weekly AMAs with the team, exclusive early access to features, featured user stories, a jobs board for our industry. And we could get power users to become moderators which gives them status and helps us scale. The community could also be a feedback goldmine if we structure it right. And then separate but related: I want to do more events. Not big expensive conferences, but small intimate dinners with twenty key customers. Cheaper, more meaningful, better relationship building. And maybe a virtual summit once a year — it's become much easier to produce and the ROI is good if done right. And then one more completely unrelated thing: I had a weird dream last night about presenting to a crowd and forgetting all my slides. Classic anxiety dream. Probably means I'm stressed about the investor meetings. I should do some breathing exercises."},
    {"id": "R018", "type": "brain_dump", "text": "So I've been thinking about team structure. We've been flat for a long time — everyone reports to me or to the CTO and it's not scaling. We need managers. But I hate the idea of creating bureaucracy. So maybe lead engineers who do mostly individual contributor work but also support two or three people each. Like a hybrid model. And on the product side, we need proper product managers. Right now the founders are doing product and it's fine but we're getting to the size where we need dedicated people who own the roadmap, talk to customers, write specs. I want to hire two PMs this quarter. And then there's the question of async versus sync culture — we've grown to have people in three time zones and the expectation of instant Slack responses is exhausting for some people. We need to write a communication norms document and actually stick to it. And — totally separate — I got a message from a former employee who wants to come back. I actually think it's a good idea. They know the codebase, they know the culture, they left on good terms. Worth exploring."},
    {"id": "R019", "type": "brain_dump", "text": "Okay so creative brainstorm for the product. What if we had a feature where users could share their workflows — like templates — with other users. So someone sets up a really clever workflow and packages it as a template that others can install with one click. That could be a massive growth driver. And then the marketplace angle — like if those templates were good enough, some people might pay for premium ones. That's a whole new revenue stream. And then there's the API thing: we're still not API-first. Everything is built for the UI. If we opened up a proper API, developers could build integrations, automations, connect it to anything. That would massively expand the use cases. And then the AI angle again: what if the AI could watch how someone uses the product and suggest improvements to their workflow. Like a personal efficiency coach built in. And then there's voice control — can users speak commands? Like 'create a new project called X' and it just does it. That's genuinely useful. Okay these are all big ideas, none of them are quick, but they're worth capturing."},
    {"id": "R020", "type": "brain_dump", "text": "So it's Sunday evening and I'm just thinking about the week ahead. Monday I have the all-hands, then the one-to-one with the engineering lead, then a call with a prospect at four. Tuesday is mostly free which is rare — I want to use it for deep work, probably work on the strategy document I've been avoiding. Wednesday there's a board observer joining the team meeting which makes me slightly nervous so I want to prep a bit. Thursday I have back to back meetings — customer, investor, then internal — it's going to be exhausting. Friday I want to end early for once, like three PM, just to actually have a break. And then I'm worried about the project that's supposed to ship this week. The developer working on it hasn't given me a confident update. I should have a direct conversation tomorrow about whether the deadline is realistic. And I should also remember to thank the customer support team — they've handled a really difficult week really well and I haven't acknowledged them properly. That matters."},
    {"id": "R021", "type": "brain_dump", "text": "Right so I've been procrastinating on thinking about this but I need to address the technical architecture issue. Our current monolith is creaking. We keep adding features and it's getting harder to maintain, harder to deploy independently, harder to scale individual components. I know the answer is microservices or at least a modular monolith as a stepping stone, but the migration is scary. It's easy to make a mess. And we don't have bandwidth to do a big bang rewrite. So I'm thinking: strangler fig pattern. Slowly extract pieces. Start with the bits that are most painful — like the notifications service which is completely tangled up with everything. And then the reporting module which has its own database access patterns. Over twelve months we could have a much cleaner system without ever stopping feature development. But we need the CTO to champion this. It can't just be something we say we'll do and never prioritise. And also completely different: I want to start a company book club. Just once a month, pick a relevant book, half hour discussion. Team building and learning combined."},
    {"id": "R022", "type": "brain_dump", "text": "Okay I'm thinking about the talent strategy because we're going to be hiring ten people in the next six months and we need to do it well. So first: we need a talent pipeline, not just reactive job postings. That means attending meetups, being visible in communities, having engineers write blog posts, open sourcing things. Second: the interview process needs to be tightened. Right now different people ask completely different questions and we have no consistency. Need a structured interview guide. Third: we've never hired internationally before and I want to think about whether that's the right move. There are amazing engineers in Eastern Europe, Latin America, Asia, who we're missing because we've been UK-focused. Fourth: diversity and inclusion. Our team is pretty homogenous right now and I know from research that diverse teams build better products. We need to be intentional about this in hiring. Fifth: the referral programme — currently we have one but nobody uses it because the reward isn't motivating enough. Increase the bonus, make it easier to refer. Sixth: the employer brand. Our careers page is terrible. Needs a rewrite. Show the culture, the work, the team. Make people want to work here."},
    {"id": "R023", "type": "brain_dump", "text": "So I'm on the train and I've got twenty minutes and I want to think about the pricing page on the website. It's confusing. There are too many tiers, the feature differentiation isn't clear, and the pricing itself doesn't map to the value we deliver. I think we should simplify to three tiers: starter, growth, enterprise. Starter is free or very cheap, enough to get value, limited to small teams. Growth is the sweet spot, most features, priced per seat. Enterprise is custom pricing, white glove support, SSO, all the compliance stuff. And the names should be simple, not clever. And each tier should have a clear hero feature that explains why you'd upgrade. Right now people have to read a massive feature comparison table to figure out which plan they need. And we should add testimonials next to the pricing — social proof at the moment of decision. And an FAQ section — the most common objections. And a clear money-back guarantee or trial offer to reduce friction. I think a good pricing page could improve conversion by a meaningful amount."},
    {"id": "R024", "type": "brain_dump", "text": "Right so partnership strategy. I've been thinking about this a lot. There are three types of partnerships worth pursuing. First: integration partners — other tools our users already use, like Slack, Notion, Salesforce. Deep integrations make our product stickier. Each integration is a growth channel too because their users discover us. Second: distribution partners — companies who could resell or bundle our product with theirs. This is harder to do well but the ceiling is massive. Third: technology partners — infrastructure providers, AI vendors, cloud platforms. These often come with co-marketing and credits which helps with cost. And then there's the question of who owns partnerships. Right now it's nobody, it's kind of shared between sales and product and nobody's driving it. We need one person to own this. And the partnership pipeline needs to be in the CRM just like sales. And — completely separate thought — I saw a meme earlier that perfectly described our company's early days and I sent it to the founders group chat and everyone laughed. It's good to laugh at yourself."},
    {"id": "R025", "type": "brain_dump", "text": "So I want to think about what success looks like in twelve months. If I imagine sitting here this time next year and feeling genuinely proud, what would be true? We'd have hit our ARR target — call it five million. The team would have grown from twenty-two to thirty-five people, but still feel tight-knit. We'd have launched in two new markets. The product would be noticeably better — faster, more reliable, more intuitive. Churn would be below five percent monthly. We'd have a handful of enterprise customers anchoring the portfolio. We'd have completed at least one significant partnership. The team would be happy — low attrition, high engagement. We'd be known in the industry. People would know who we are. And personally: I'd be working smarter not harder. More delegation, more thinking time, better habits. Okay so if that's the destination, what are the three things that matter most in the next ninety days to put us on that path? I think: one, nail the product fundamentals so retention improves. Two, close the Series A. Three, hire the key people — head of engineering, two PMs, head of marketing. Everything else is noise."},

    # ── Self-correcting rambles (20) ──
    {"id": "R026", "type": "self_correcting", "text": "So I want to — wait, actually before that, let me — no okay so the feature I want to build is a dashboard — actually not a dashboard, more like a report, like a weekly email summary — no wait, it should be in-app, not email — okay so an in-app weekly digest showing the user's top metrics."},
    {"id": "R027", "type": "self_correcting", "text": "The deadline for this should be — I want to say end of month — actually no, that's too soon — let's say mid next month — hmm actually we have the release on the fifteenth — okay, end of next month, that's more realistic."},
    {"id": "R028", "type": "self_correcting", "text": "Build this in React — actually, you know what, the backend is Python so maybe Flask — wait no, the frontend needs to be separate — okay React for frontend, Flask API for backend, that's the right call."},
    {"id": "R029", "type": "self_correcting", "text": "I want to hire three engineers — actually wait, two is more realistic given budget — hmm but we really need the capacity — okay let's say two senior and one junior, that's three but the cost is more like two and a half."},
    {"id": "R030", "type": "self_correcting", "text": "Message to the client: we're going to deliver by Friday — wait no, Thursday — actually I need to check with the team first — okay message to the client: we're targeting Thursday delivery, will confirm by Wednesday."},
    {"id": "R031", "type": "self_correcting", "text": "The new feature should use PostgreSQL — hmm actually we're already on MongoDB for most things — should I switch? — no, consistency matters — okay use MongoDB, but for the analytics queries we'll need a separate reporting database."},
    {"id": "R032", "type": "self_correcting", "text": "I'm going to call it — what's a good name — 'SmartView' — that sounds generic — 'InsightBoard' — too corporate — 'Flow' — too vague — okay just call it 'Weekly Review', descriptive is better than clever."},
    {"id": "R033", "type": "self_correcting", "text": "Let's price this at nineteen ninety nine a month — actually that feels too low — twenty-nine ninety nine — wait that's an odd number, nobody likes odd numbers — thirty dollars — yeah, thirty dollars a month feels right."},
    {"id": "R034", "type": "self_correcting", "text": "I should assign this to Tom — actually Tom is already overloaded — what about Sarah — she's on leave this week — okay Dave then — yeah Dave can do this, he's been looking for something new to own."},
    {"id": "R035", "type": "self_correcting", "text": "We should deploy this on AWS — hmm we're already paying for Google Cloud and we shouldn't have two cloud bills — okay Google Cloud then — actually the team knows AWS better — you know what, we'll use Google Cloud since that's what we have."},
    {"id": "R036", "type": "self_correcting", "text": "The standup should be at nine AM — that's too early for the New York team — let's say ten AM London time which is five AM New York — that's terrible — okay two PM London, nine AM New York, that works for everyone."},
    {"id": "R037", "type": "self_correcting", "text": "I'll write it in TypeScript — actually this is a quick script, Python is faster to write — hmm but TypeScript is more maintainable long term — okay if it's going to be maintained, TypeScript, if it's a one-off script, Python. This is going to be maintained so TypeScript."},
    {"id": "R038", "type": "self_correcting", "text": "Target launch date: Q1 — that's already almost over — Q2 then — that's only three months away — is that enough time? — it's tight but yes if we scope it well — okay Q2, but scope it aggressively."},
    {"id": "R039", "type": "self_correcting", "text": "I want to send this as a push notification — actually users have turned those off mostly — email then — nobody reads emails anymore either — in-app message — yeah, in-app message is the right call."},
    {"id": "R040", "type": "self_correcting", "text": "The meeting venue should be the office — actually half the team is remote — Zoom then — people are zoomed out — hybrid — a Teams call with people in the office also joining — no that's terrible — okay just Zoom, it is what it is."},
    {"id": "R041", "type": "self_correcting", "text": "Store user data in the cloud — actually for privacy we should keep sensitive data on-device — but then backup is a problem — hmm, end-to-end encrypted cloud backup — yes, that's the right model, data is encrypted on device before upload."},
    {"id": "R042", "type": "self_correcting", "text": "Maximum file size should be — ten megabytes? — that might be too small for some users — twenty? — hmm, fifty megabytes — that could get expensive with storage — let's say twenty-five megabytes for free tier, unlimited for paid."},
    {"id": "R043", "type": "self_correcting", "text": "I want to do a webinar — how many people do we invite — all customers — that's four hundred, too many — top hundred by usage — or should it be the churning ones — no, do it for top customers to retain and grow them, separate event for at-risk."},
    {"id": "R044", "type": "self_correcting", "text": "The sprint length should be one week — actually we tried that and the planning overhead was too high — two weeks — yeah, stick with two weeks, it works."},
    {"id": "R045", "type": "self_correcting", "text": "I want to auth with email and password — wait we should add Google OAuth too — and Apple sign in for mobile — okay so: email/password, Google OAuth, and Apple sign-in. Those three cover ninety-nine percent of users."},

    # ── Emotional/stressed speech (15) ──
    {"id": "R046", "type": "emotional_stressed", "text": "Okay so like the demo is in two hours and the thing is still broken and I don't know what to do like I've been up since four trying to fix this and it's still not working and I need someone to help me right now like actually right now because I cannot go into that meeting with a broken product."},
    {"id": "R047", "type": "emotional_stressed", "text": "Right I'm really frustrated because I asked for this to be done by last Friday and it's still not done and I have a client screaming at me and I just need someone to take ownership and actually finish it today, not tomorrow, today."},
    {"id": "R048", "type": "emotional_stressed", "text": "I can't believe this, the server went down in the middle of peak traffic, we've got like hundreds of users affected, we need to get on a war room call right now, who's available, I need the senior engineers on this immediately."},
    {"id": "R049", "type": "emotional_stressed", "text": "I'm really worried about this project, like genuinely worried, it's three weeks behind schedule, the client is getting impatient, the team seems demotivated, and I feel like I don't have a clear view of where we actually are or what the risks are."},
    {"id": "R050", "type": "emotional_stressed", "text": "So I got the worst news this morning, like genuinely bad, our biggest customer just told us they're not renewing. That's like thirty percent of our revenue. I don't know what to do. I need to think. Okay. Deep breath. First thing: understand why. I need to get on a call with them today and understand the actual reason."},
    {"id": "R051", "type": "emotional_stressed", "text": "Right I'm panicking a little bit, the build just failed right before the release window and I don't know why, Jenkins is showing a cryptic error, nobody's responding on Slack, I need someone to look at this RIGHT NOW."},
    {"id": "R052", "type": "emotional_stressed", "text": "This is so frustrating, I've been trying to explain this to the stakeholders for three weeks and nobody listens and now the thing I warned them about has happened and we're in a mess and I just — I need to not be angry about this and instead figure out how to fix it."},
    {"id": "R053", "type": "emotional_stressed", "text": "I'm exhausted and I know I'm not thinking clearly but I need to capture this before I forget: the deployment script from last night broke the staging environment and the backup from before the script ran is missing and I have no idea what state we're in."},
    {"id": "R054", "type": "emotional_stressed", "text": "Okay I'm stressed but trying to stay calm. The situation is: the investor meeting is tomorrow morning, the pitch deck isn't done, three sections still need data, and the person who had the data went on holiday and is unreachable. I need solutions not panic."},
    {"id": "R055", "type": "emotional_stressed", "text": "I just had the most awful meeting of my career. The client basically said everything we built was wrong and they want to start over. I don't even know if that's right or just frustration but I need to debrief, figure out what happened, and decide next steps. Can someone from the project team meet this afternoon?"},
    {"id": "R056", "type": "emotional_stressed", "text": "I need to say this before I lose it: the codebase is a mess, nobody owns anything, everything is held together with duct tape, and every time we try to add a feature we break something else. I'm not blaming anyone but this has to change. We need to stop and fix the foundations before we add anything else."},
    {"id": "R057", "type": "emotional_stressed", "text": "I'm having one of those days where nothing is going right. Three bugs in production, a sick team member, a client complaint, and a board member asking for a report I haven't started. I need to triage: what actually needs attention today and what can wait."},
    {"id": "R058", "type": "emotional_stressed", "text": "Okay I know I sound stressed and I am but let me just get this out: we promised feature X in the contract and now I'm being told by engineering that feature X is actually really complicated and is going to take three months. That's a contract issue. I need legal advice before I say anything to the client."},
    {"id": "R059", "type": "emotional_stressed", "text": "I really don't know what to do about this team conflict. Two senior developers fundamentally disagree on the architecture and neither will back down and it's blocking the whole team. I need to step in but I don't want to pick sides and make things worse. I need to facilitate a proper architectural decision process."},
    {"id": "R060", "type": "emotional_stressed", "text": "Right so I've been avoiding thinking about the performance issue but I can't anymore. The app is slow. Users are complaining constantly. We've done some optimisation but it hasn't moved the needle. We need a proper performance audit — profiling, bottleneck identification, fix the top three things, measure. And we need to do it this sprint, not next sprint."},

    # ── Topic-hopping (15) ──
    {"id": "R061", "type": "topic_hopping", "text": "So the new coffee machine in the office is great, totally worth it. Oh and also I wanted to say the API refactor is done, Tom merged it this morning. And my gym session this morning was terrible, I think I slept wrong. And also we need to renew the SSL certificate, it expires in two weeks."},
    {"id": "R062", "type": "topic_hopping", "text": "Right, three things: first the product, we need to fix the mobile scrolling bug. Second, personal, I want to take a holiday in April, probably Spain. Third, work again, can someone write the Q1 report this week."},
    {"id": "R063", "type": "topic_hopping", "text": "So the investor call went well, they want a second meeting. My daughter's birthday is on Saturday so I need to get a cake sorted. The new design mockups from the designer look amazing. And also the CI pipeline keeps failing on Wednesdays for some reason, which is weird."},
    {"id": "R064", "type": "topic_hopping", "text": "Number one: I need to buy a new monitor, mine is dying. Number two: the user research sessions start next Tuesday. Number three: I should call my brother, we haven't spoken in weeks. Number four: the marketing budget needs reallocation, we're underspending on paid and overspending on events."},
    {"id": "R065", "type": "topic_hopping", "text": "So the thing about the backend refactor is it's taking longer than expected. Also my back has been killing me, I think it's the new chair. Oh and the launch event venue needs to be confirmed by Thursday or we lose the deposit. And I want to try that new ramen place for lunch."},
    {"id": "R066", "type": "topic_hopping", "text": "One, the team needs better documentation practices — I keep having to explain things that should be written down. Two, I'm going to take a course on negotiation, I've been meaning to for ages. Three, the customer success handoff process is broken, sales is closing deals and CS doesn't know about them until a week later. Four, I need new running shoes."},
    {"id": "R067", "type": "topic_hopping", "text": "So the app store reviews are mostly positive but there are a cluster of complaints about battery drain. That's engineering's problem to diagnose. On a personal note I'm really enjoying the audiobook I'm listening to — Sapiens. And also the quarterly financial review is next Monday and I'm not prepared. And my coffee subscription needs cancelling."},
    {"id": "R068", "type": "topic_hopping", "text": "Okay so I need to: one, finalise the job description for the UX role, two, look up flights to Berlin for the conference, three, check if we have any space in the office for two more desks, four, respond to that journalist's email about our funding round."},
    {"id": "R069", "type": "topic_hopping", "text": "The user churn report is alarming — up seven percent month on month. Also I want to reorganise my home office, it's chaotic. And the partnership with the payments company needs a legal review before we can proceed. And I should get my car MOT booked."},
    {"id": "R070", "type": "topic_hopping", "text": "Right so on the product side: we're launching the new onboarding flow next sprint. Personal side: I'm trying to eat better, less sugar. Business side: we need to think about what happens to the team if the fundraise takes longer than expected. Tech side: we should evaluate whether to upgrade to the new version of our database."},
    {"id": "R071", "type": "topic_hopping", "text": "So I was reading an article about remote work productivity this morning and it made me think we should redesign our async workflows. Also my laptop battery swells up and I need to get it replaced. And the content calendar for the next quarter isn't started. And I owe someone an apology, I was quite short with them in the meeting yesterday."},
    {"id": "R072", "type": "topic_hopping", "text": "Things on my mind: the GDPR audit coming up in April, the team offsite planning for May, my prescription needs renewing, and the front page of the website is converting badly — we should A/B test the hero section."},
    {"id": "R073", "type": "topic_hopping", "text": "So we got amazing press coverage this week — Techcrunch mentioned us, that's huge. And I also really need to get to the post office, I have a parcel to collect. The engineering team is asking for clearer product specs before starting the next feature. And I should probably call the accountant before end of month."},
    {"id": "R074", "type": "topic_hopping", "text": "Okay so: the sales pipeline looks healthy for Q2. I want to read more fiction this year. The new customer dashboard design needs stakeholder sign-off. We should explore a Notion integration. And I haven't been sleeping well — need to get off screens earlier."},
    {"id": "R075", "type": "topic_hopping", "text": "So the big things today: the outage post-mortem needs to be published. I need to buy a birthday present for my colleague. The content team needs a decision on the editorial calendar strategy. And the new joiner starting Monday doesn't have a laptop yet, someone needs to sort that today."},

    # ── Planning rambles (15) ──
    {"id": "R076", "type": "planning", "text": "Okay so I'm trying to think through the plan for launching this feature. So first we need to finish the development obviously — that's probably two more weeks. Then QA, which usually takes a week, but this one is complicated so let's say ten days. Then we need to write the release notes and update the docs, that's maybe two days. Then a staged rollout — start with five percent of users, watch the metrics, then ramp up. If everything looks good we go full rollout. Total: roughly a month. That feels doable."},
    {"id": "R077", "type": "planning", "text": "So how do I structure this project? Right, phase one is research — talk to users, understand the problem space, two weeks. Phase two is design — wireframes, prototypes, user testing, three weeks. Phase three is build — front end and back end, six weeks. Phase four is QA and staging — two weeks. Phase five is launch and monitoring — ongoing. So that's roughly thirteen weeks from now to launch. Call it four months with buffer."},
    {"id": "R078", "type": "planning", "text": "I'm trying to plan the investor pitch meeting. So the agenda: first ten minutes, introductions and overview of the company. Then twenty minutes on the product demo. Then ten minutes on the market and competition. Then ten minutes on financials and the ask. Then ten minutes Q&A. That's an hour. I should prepare a one-page summary doc to send in advance and make sure the demo environment is stable. And I need to know who'll be in the room from their side."},
    {"id": "R079", "type": "planning", "text": "So thinking about the team offsite. So we need a venue — somewhere within two hours of London, a nice country house or hotel, not too corporate. Two nights, so arrive Thursday evening, full day Friday, Saturday morning then back. Agenda for Friday: morning is strategy and planning, afternoon is workshops and team building, evening is dinner. Saturday morning: review and actions. Budget: probably five hundred per person all in. Team is twenty people so ten thousand. Is that okay? That feels like a stretch but it's worth it for team cohesion."},
    {"id": "R080", "type": "planning", "text": "How do I approach the difficult performance conversation? So first: make sure I'm going in with facts, not feelings. Pull the specific examples. Second: start with curiosity, not accusation — 'help me understand what's been happening' rather than 'you've been underperforming'. Third: be clear about the gap between what's needed and what's happening. Fourth: collaboratively build a plan — what support do they need, what will change, by when. Fifth: document it and follow up in two weeks. Keep it human, keep it direct."},
    {"id": "R081", "type": "planning", "text": "Okay so the plan for migrating the database. Step one: audit what we currently have — tables, relationships, volumes, dependencies. Step two: design the new schema. Step three: build the migration scripts and test them on a copy of production data. Step four: staging rollout and validation. Step five: production migration during low-traffic window — probably Sunday three AM. Step six: monitor for twenty-four hours. Have a rollback plan ready. Timeline: six weeks if we start now. Resources: one DBA, one backend engineer, part-time."},
    {"id": "R082", "type": "planning", "text": "So I'm trying to figure out the hiring plan for H2. We need: one head of engineering, two mid-senior backend engineers, one front-end engineer, two product managers, one head of marketing, one customer success manager. That's nine people. Average time to hire is eight weeks. So if we start posting now we could have most people by September. Budget: we need to factor in recruiting fees if we use agencies — probably fifteen percent of salary. And I want to avoid agencies where possible — referrals and direct sourcing are better quality."},
    {"id": "R083", "type": "planning", "text": "Right, the go-to-market plan for the enterprise tier. Phase one: identify twenty target accounts, do deep research on each, understand their pain points. Phase two: warm outreach — through LinkedIn, network connections, industry events. Phase three: discovery calls, understand fit, tailor the pitch. Phase four: proof of concept or pilot for serious prospects. Phase five: contract and close. Timeline: three months from first contact to close for enterprise. We need someone dedicated to this — a senior account executive. Hire by end of month."},
    {"id": "R084", "type": "planning", "text": "So the content marketing plan. First: define the audience and what they care about. For us it's engineering leaders, product people, and startup founders. Second: decide on formats — blog posts, maybe a podcast, definitely short-form video for LinkedIn. Third: create a content calendar — two posts a week minimum. Fourth: build distribution — newsletter, social, communities. Fifth: measure — what does success look like? Traffic, leads, newsletter subscribers, shares. Timeline: first piece of content in two weeks. First month: establish cadence. First quarter: see first meaningful results."},
    {"id": "R085", "type": "planning", "text": "Okay so the plan for dealing with this outage. Right now: get the site back up, that's priority one. Senior engineer needs to be on this immediately. Second: once it's up, a full assessment — what broke, how long was it down, how many users affected. Third: customer communication — send an apology, explain what happened without being too technical, give an ETA for the full RCA. Fourth: post-mortem within forty-eight hours, no-blame, focused on systemic fixes. Fifth: implement the fixes. Sixth: add monitoring to prevent recurrence. Seventh: follow up with affected customers personally if they're enterprise."},
    {"id": "R086", "type": "planning", "text": "How do I structure the next six months personally? So I want to hit four main goals: one, run a half marathon — that means I need to be running properly by end of month, following a training plan. Two, read twelve books — one a month, I have a list, stick to it. Three, learn a new skill — I've been wanting to learn data science fundamentals, there's a Coursera course I've bookmarked. Four, improve my financial situation — build up a six-month emergency fund. For each of these I need to break them down into monthly milestones and check in on them at the end of each month. Put a recurring reminder in."},
    {"id": "R087", "type": "planning", "text": "So how do we plan the rebranding? Phase one: discovery — what do we stand for, what do we want to be known for, what's the brief. Two to three weeks. Phase two: agency selection — shortlist three agencies, review portfolios, brief them, get proposals. Three weeks. Phase three: creative development — logo, colours, typography, visual language. Four weeks. Phase four: brand guidelines document. One week. Phase five: application — update website, app, collateral, social. Four weeks. Phase six: internal rollout then external announcement. Two weeks. Total: about four months. Budget: agency fees will be the biggest cost, probably thirty to fifty K for a good rebrand."},
    {"id": "R088", "type": "planning", "text": "Right I want to think through how to structure the week to be more productive. Monday: planning day — review the week ahead, set priorities, do not put meetings before eleven. Tuesday and Wednesday: deep work — protected time, no standing meetings, focus on the highest leverage work. Thursday: meetings day — all one-to-ones, team syncs, external calls, batch them. Friday: review day — what got done, what didn't, what carries over, clear the inbox, plan next week. Mornings: before nine AM, exercise, breakfast, reading, no work. This is the structure. Now I need to actually implement it."},
    {"id": "R089", "type": "planning", "text": "Okay so how do I approach learning this new technology? Right. Step one: get the fundamentals — find a good course or book, commit to one hour a day for two weeks. Step two: build something small — a toy project, doesn't need to be useful, just needs to use the core concepts. Step three: build something real — apply it to an actual problem I have. Step four: share what I learned — blog post or internal talk, teaching is the best way to cement knowledge. Step five: go deeper in the areas that matter most for my use case. Six weeks from zero to actually useful. Let's do it."},
    {"id": "R090", "type": "planning", "text": "So the plan for the beta programme. We're going to invite fifty users from the waitlist. The criteria: they should be in our target market — startup founders or product leads at companies with five to fifty people. Mix of technical and non-technical. Diverse industries. Send them a personalised invite explaining what we're doing and what we need from them. During the beta: weekly survey, access to a private Slack channel with the team, optional weekly feedback call. Duration: six weeks. What we're measuring: activation rate, daily active usage, key feature usage, NPS at end, and qualitative feedback on what's missing. At the end of beta, offer a founding member discount to convert them to paying customers."},

    # ── Very long single-topic deep dives (10) ──
    {"id": "R091", "type": "deep_dive", "text": "So I want to think really deeply about the customer acquisition strategy because I don't think we've been systematic about it at all. Right now we get customers through a combination of word of mouth which is great but unpredictable, some paid acquisition which is expensive and I'm not sure about the ROI, a bit of content marketing which we do inconsistently, and cold outreach which the sales team does with mixed results. What I want to do is really understand each channel. So let's start with word of mouth. Word of mouth works when customers are genuinely delighted and when the product is naturally shareable. I think we can do more to engineer virality — like making the output of the product something people want to share, or having a collaboration feature that brings colleagues in, or having a referral programme with a genuine incentive. And we need to make it easy for happy customers to leave reviews — G2, Capterra, Trustpilot — because those have a long tail of discovery. For paid acquisition, I think we need to be much more systematic about measurement. Right now I don't think we know the CAC by channel with any confidence. We need proper attribution modelling and we need to understand the LTV to CAC ratio by segment. Without that we're flying blind. For content marketing, the problem is consistency. We write a few posts, stop, write a few more. We need to treat it like a product — it has to ship on a schedule regardless of whether inspiration strikes. And the content has to be genuinely valuable, not just SEO-optimised fluff. What problems do our users have? What questions do they ask? Answer those. For cold outreach, I think the issue is targeting. We cast too wide. We should narrow down to a specific ICP — ideal customer profile — and get really good at reaching just them. And the messaging needs to be much more about their pain than about our features. What I'd love to do is run a ninety-day experiment: pick one channel, invest in it properly, measure it rigorously, and see if we can make it work before spreading attention. My instinct is that content marketing is the one with the best long-term ROI but it requires patience which is hard when you're fundraising."},
    {"id": "R092", "type": "deep_dive", "text": "Okay so I want to do a really deep think on the product strategy and where we're heading. Right so the core product is working and people like it. The retention is okay but not great. The question is what do we build next. There are basically three directions we could go. Direction one: go deeper on the core use case. Make the thing we do already even better. Add more power features, improve the performance, make it more reliable. This is the safe bet. It improves retention and makes the product more defensible. The risk is it might not excite new users. Direction two: go broader. Add adjacent use cases. We currently serve one workflow, we could serve three or four related workflows. This grows the addressable market. The risk is we spread thin and end up being okay at multiple things rather than great at one. Direction three: go up market. Focus on enterprise. Bigger deals, longer sales cycles, more requirements around security and compliance, but much higher ACV. The risk is it changes the culture of the company and distracts from the SMB market that got us here. And I think there's actually a fourth direction which is platform — open up APIs, build an ecosystem, let others build on top of us. That's a very different bet, longer term, but the ceiling is much higher. My gut says we should go deeper first. Get the core experience so good that customers become advocates. Then selectively go broader into adjacent use cases that serve the same core user. Enterprise can come later when we have the processes to support it. Platform is a two to three year play. Does that make sense as a framework? I think so. The test I'd apply to any feature request: does this make the core better, or does it expand us into a new area? If it's the latter, it needs a higher bar to justify."},
    {"id": "R093", "type": "deep_dive", "text": "So I want to think hard about the engineering hiring strategy because we've made some mistakes and I want to do it better. The mistakes we've made: we've hired too fast and not been selective enough when we were under pressure. We've hired people who are technically good but not a culture fit and that's caused friction. We've hired people who are great individually but not collaborative which in a startup is a problem. And we've sometimes hired for today's needs without thinking about where we need to be in twelve months. So what does better hiring look like? First, get the bar right. We need to know what outstanding looks like in every role, not just adequate. Second, the process needs to be consistent. Right now different interviewers ask completely different questions and weight things differently. We need a structured interview guide. Third, values and culture fit needs to be explicitly assessed. Not just implicitly. Have specific questions about how they've handled disagreement, ambiguity, failure. Fourth, we should always be talent sourcing even when we don't have open roles. Build the pipeline proactively. Fifth, the hiring manager needs to do reference checks personally, not delegate them. The information you get is so much richer. Sixth, make the decision quickly. Top candidates have multiple offers. If we think someone is great we should move within days not weeks. And seventh — and this is important — be honest about the role and the company. Don't oversell. The people who stay long-term are the ones who joined knowing what they were getting into. And then on the diversity angle: we need to proactively source from underrepresented groups. That means working with specific communities, changing job description language, ensuring diverse interview panels. It won't happen by accident."},
    {"id": "R094", "type": "deep_dive", "text": "So I want to do a really thorough think about our data strategy because right now it's a mess and it's getting worse as we scale. So the fundamental problem is we have data in too many places. We have the production database, which is Postgres. We have a separate MySQL instance that was set up years ago for a specific thing and never migrated. We have data in our analytics tool — Mixpanel. We have data in our CRM — HubSpot. We have data in our support tool — Intercom. And we have a bunch of CSV exports that live on a shared drive and nobody knows if they're current. So when someone asks a business question, the answer might live across three or four of these systems and getting to it requires manual work. What we need is a proper data warehouse where we pipe everything. I'm thinking Snowflake or BigQuery. BigQuery is probably simpler to start. Then we use a tool like Fivetran or Airbyte to pipe data from all the sources. Then we build a dbt layer for transformations. Then we put a BI tool on top — Metabase is good for self-serve, Looker if we need something more enterprise. And then critically: we need a data dictionary. What does every metric mean. What is the definition of 'active user'. What counts as a 'conversion'. These definitions need to be agreed and documented and enforced. The amount of time we waste arguing about numbers because people are measuring different things is enormous. This is a three to four month project. It needs one data engineer and one analyst to own it. The business impact is huge — better decisions, less manual work, more confidence in the numbers."},
    {"id": "R095", "type": "deep_dive", "text": "So I've been thinking for a long time about our approach to technical debt and I want to actually write it down and make it a policy. So the way I see it, technical debt falls into three categories. Category one is intentional short-term debt — we made a conscious decision to cut a corner to ship faster, and we have a plan to fix it later. This is sometimes okay. Category two is unintentional debt — we wrote something and didn't realise it was going to cause problems, or the requirements changed after we built it. This is inevitable. Category three is reckless debt — we knew it was wrong but we did it anyway and never came back to it. This is the dangerous one and it's where I think we have the most. The problem with reckless debt is it compounds. Every time you touch that part of the code it takes longer, you introduce more bugs, you're afraid to change things. So what's the policy? One: we reserve twenty percent of every sprint for debt reduction. Not feature work. Explicitly debt work. Two: when someone identifies a piece of debt, they create a ticket immediately, don't let it live only in their head. Three: the debt backlog is reviewed and prioritised monthly by the tech lead. Four: before adding a new major feature, the team does a quick assessment of the debt in that area and decides if it needs to be addressed first. Five: we never ship to production code we're ashamed of. If it's not good enough, we take the time to make it good enough. And six: we measure and track technical health metrics — build times, test coverage, incident frequency — so we can see if we're improving."},
    {"id": "R096", "type": "deep_dive", "text": "So the thing I want to think through is our approach to machine learning features because I feel like we keep talking about it without ever making it concrete. Let me try to actually think this through. So the use cases that are most compelling for our product: number one, smart categorisation — users have to manually categorise things right now and it's tedious. A model trained on how they've historically categorised things could automate eighty percent of this. Number two, anomaly detection — flag when something looks unusual in their data. This is genuinely useful and drives engagement because users open the app to see the alert. Number three, smart recommendations — based on what similar users do, suggest what this user might want to do next. Number four, natural language queries — instead of building reports through a UI, just ask in plain English. That last one is the most exciting but also the most complex. Now the question is build versus buy. For categorisation and anomaly detection we could build them ourselves — they're not that complex, good feature engineering and a decent model would be fine. For natural language, that's better done by leveraging an LLM API — OpenAI or Anthropic. The cost would be manageable at our scale. What I want to avoid is building AI features for the sake of it. Each one should have a clear user problem it solves and a way to measure whether it's working. And we should be honest with users about what the AI can and can't do. Trust is earned slowly and lost quickly. I think the right first bet is the categorisation one — it has high visibility, it's achievable in one sprint, and it immediately saves users time. Then anomaly detection. Then the rest."},
    {"id": "R097", "type": "deep_dive", "text": "I want to think through the fundraising strategy properly because I've been a bit scattered about it. So we're raising a Series A, targeting four to six million, to fund roughly eighteen months of runway. The story is: we have product-market fit demonstrated by the retention and NPS numbers, we have revenue growing twenty percent month-on-month, we have a clear GTM playbook that the capital will help us execute faster. The lead investor is the most important decision. We want someone who understands the space, has portfolio companies we can learn from, and will be value-add not just a check writer. I have a shortlist of six firms. For the process: I want to run a tight six-week process — week one is warm-up and outreach, week two and three are first meetings, week four is follow-up and due diligence, week five is term sheet negotiations, week six is closing. The worst thing is a long drawn-out process because it distracts from the business. Key metrics I need to nail in the pitch: MRR and growth rate, net revenue retention — ideally over a hundred percent — CAC and LTV, churn rate, and the growth opportunity — the addressable market size and why we can win it. Objections I need to prepare for: why won't a big company build this, what's the moat, why you as founders. And I need the data room set up before we get into due diligence — financials, cap table, contracts, incorporation docs, employment agreements. Okay I think I have a plan. Let me write it up properly as a document this week."},
    {"id": "R098", "type": "deep_dive", "text": "So I want to do a really deep think about our security posture because I don't think we take it seriously enough. We handle sensitive customer data and we have a responsibility. Let me go through the main areas. First: authentication and access control. We should be using SSO and enforcing MFA for all internal systems. We should have the principle of least privilege — people have access to exactly what they need, nothing more. We should audit access quarterly and remove people who've left or changed roles. Second: data encryption. Data in transit should be TLS everywhere. Data at rest should be encrypted. Encryption keys should be managed properly, ideally with a secrets manager. Third: application security. We should be doing code reviews with security in mind. We should have automated security scanning in the CI pipeline — something like Snyk or SonarQube. We should do penetration testing at least annually. We should have a responsible disclosure policy so researchers can report vulnerabilities. Fourth: incident response. We need a documented process. Who gets called first. How do we assess severity. How do we communicate with customers. What's the post-mortem process. Fifth: compliance. If we have European customers, GDPR compliance is not optional. We need a proper data processing agreement, a data retention policy, and the ability to respond to subject access requests. If we want enterprise customers we'll need SOC 2, which is a significant commitment but increasingly expected. I want to do a gap assessment against all of this and build a security roadmap. This should be owned by the CTO with exec visibility."},
    {"id": "R099", "type": "deep_dive", "text": "So I've been thinking about remote versus in-person work culture and I want to figure out our actual position rather than just defaulting to whatever is convenient. So the arguments for more in-person: spontaneous collaboration is genuinely harder remote. Onboarding new people is harder. Culture is harder to build. Creative work can be slower. And there's something about shared physical space that builds trust faster. The arguments for remote: we can hire the best people regardless of location. People have better work-life balance. The office is a real cost. And frankly some of our best work has been done fully remotely. So I think the answer is neither extreme. A fully distributed company loses too much of the magic of being together. But mandating five days in the office is a great way to lose your best people. So what's the model? I think: expectations of in-person collaboration when it matters — big planning sessions, onboarding, team offsites, launch moments. But flexible for day-to-day work. If you're in the same city as the office, maybe come in two or three days a week. If you're remote, we fly you in for quarterly gatherings at minimum. The key principle: the mode of work should be determined by the work, not by habit or assumption. Some tasks are better remote, some are better in person. Trust people to know the difference. And critically: we don't create a two-tier culture where in-office people are advantaged. Every meeting is a remote meeting in the sense that if one person is remote, we all use the same tools and treat everyone equally. That's a cultural commitment."},
    {"id": "R100", "type": "deep_dive", "text": "Okay so I want to think through the competitive landscape really carefully because I feel like we wave our hands at it but don't really have a sharp view. So the main competitors I can think of: Competitor A is the biggest player in the market. They have more features, more brand recognition, bigger sales team. But they're expensive, they're slow to innovate, and their UX is notoriously bad. Users use them because they have to, not because they love them. Competitor B is newer, more modern UX, venture-backed, growing fast. They're probably our most direct competitor. They're stronger on the consumer side than we are. Their enterprise story is weaker. Competitor C is actually not software — it's the way people currently do this manually, with spreadsheets and email. This is probably our biggest actual competitor because it's the status quo. And then there are point solutions — tools that do one piece of what we do but don't do the full workflow. So how do we think about positioning against each? Against Competitor A: we're simpler, faster, better UX, better value. Against Competitor B: we're stronger on integrations, better suited to teams, more customisable. Against the manual approach: we're faster, fewer errors, better visibility, saves real time. Against point solutions: we're integrated, you don't need five tools, everything in one place. The through-line is: we're the modern, integrated, team-first solution. And I genuinely believe that's true, it's not just marketing. But we need to say it louder and more consistently. Our messaging should make it immediately clear who we're for and why we're better."},
]

AGENTIC_TRANSCRIPTS = [
    # ── Build requests (25) ──
    {"id": "A001", "type": "build_request", "text": "I want to build a simple web app that lets users upload a CSV file and it automatically generates charts from the data. Should work in the browser, no backend needed, just JavaScript. Show bar charts, line charts, auto-detect column types."},
    {"id": "A002", "type": "build_request", "text": "Build me a Python script that monitors a folder for new files and when a file is added, it automatically compresses it, moves it to an archive folder, and logs the event with timestamp."},
    {"id": "A003", "type": "build_request", "text": "Um so I need a Chrome extension that, uh, when I highlight text on any webpage, adds a button that lets me save it to a personal notes file — just plain text, nothing fancy, and it should show me how many notes I've saved."},
    {"id": "A004", "type": "build_request", "text": "Build a REST API in Node.js with Express that has endpoints for managing a todo list — create, read, update, delete. Use PostgreSQL as the database and include proper input validation. Deploy-ready with Docker."},
    {"id": "A005", "type": "build_request", "text": "I want to build a CLI tool in Python that takes a GitHub repository URL and generates a markdown summary of the repo — what it does, the file structure, the main dependencies, and a getting started section."},
    {"id": "A006", "type": "build_request", "text": "Build a real-time chat application — React frontend, Node.js backend, WebSockets for real-time messaging, rooms feature, user names, and message history that persists in Redis."},
    {"id": "A007", "type": "build_request", "text": "So like, um, I want to build, you know, a dashboard that, uh, basically pulls in data from our Stripe account and shows key metrics — MRR, new subscriptions, churn, average revenue per user — and updates automatically every hour."},
    {"id": "A008", "type": "build_request", "text": "Create a Python script that uses the OpenAI API to generate daily standup summaries. It reads from a Jira board, finds all tickets updated in the last twenty-four hours, and writes a human-readable standup in bullet point format."},
    {"id": "A009", "type": "build_request", "text": "I want a web scraper in Python that monitors a competitor's pricing page and sends me a Slack notification whenever the prices change. Use BeautifulSoup, store the previous state in a JSON file, run via cron."},
    {"id": "A010", "type": "build_request", "text": "Build a Next.js landing page for a SaaS product — hero section with headline and CTA, feature grid, pricing table with three tiers, testimonials, and a footer. Tailwind CSS, mobile responsive, deploy to Vercel."},
    {"id": "A011", "type": "build_request", "text": "Um, so, build me a tool — it can be a script or a small app, doesn't matter — that takes a folder of images and automatically resizes them to web-optimised sizes, converts to WebP, and renames them with a consistent naming convention."},
    {"id": "A012", "type": "build_request", "text": "I want to build a personal finance tracker. A web app, React frontend, FastAPI backend. Users can add transactions, categorise them, see spending trends over time as charts, and set monthly budgets per category."},
    {"id": "A013", "type": "build_request", "text": "Build an automated email newsletter system — Python, takes a list of subscribers from a CSV, personalises each email with their name, and sends via SendGrid. Track open rates and include an unsubscribe link."},
    {"id": "A014", "type": "build_request", "text": "So the idea is, kind of, a browser-based markdown editor — like Typora but simpler — with live preview, syntax highlighting, dark mode, and the ability to export to PDF. No accounts, no cloud, everything local."},
    {"id": "A015", "type": "build_request", "text": "Build a Discord bot in Python that monitors a subreddit for new posts matching specific keywords and posts them in a Discord channel with a summary. Use PRAW for Reddit, discord.py for the bot."},
    {"id": "A016", "type": "build_request", "text": "I want a command-line tool that generates a boilerplate project structure for a given framework — React, Vue, FastAPI, or Express — takes the project name as input and sets up the folder structure, basic config files, and installs dependencies."},
    {"id": "A017", "type": "build_request", "text": "Build a weather app — React, use the OpenWeatherMap API — shows current weather and a five-day forecast, hourly chart, and lets users search by city or use their current location. Clean minimal design."},
    {"id": "A018", "type": "build_request", "text": "So basically what I need is, uh, a Python script that, like, reads from a Google Sheet, processes the data, and writes a formatted report to a Word document. It's a weekly report that I do manually right now and I want to automate it."},
    {"id": "A019", "type": "build_request", "text": "Build a rate limiting middleware for Express that limits requests per IP, configurable requests-per-minute, stores the rate limit state in Redis, returns a 429 with a Retry-After header when exceeded."},
    {"id": "A020", "type": "build_request", "text": "I want a full-stack bookmarks app — save URLs with tags, search by tag or URL, a browser extension to save pages quickly, everything synced to a backend. Use SvelteKit for the frontend and Supabase for the backend."},
    {"id": "A021", "type": "build_request", "text": "Create a Python script that monitors our company's uptime — check a list of URLs every five minutes, log the response time and status code, send an email alert if anything is down for more than two consecutive checks."},
    {"id": "A022", "type": "build_request", "text": "Build me a Notion-like simple document editor — rich text editing, headings, bullet lists, code blocks, drag and drop reordering, auto-save to local storage. React with a good rich text library like Tiptap."},
    {"id": "A023", "type": "build_request", "text": "I want to build an AI writing assistant web app — text area where you type, a button to 'improve' the writing using GPT-4o, shows diff of what changed, accepts or rejects the changes. Minimalist, distraction-free."},
    {"id": "A024", "type": "build_request", "text": "Build a Kubernetes operator in Go that monitors a custom resource — a 'scheduled backup' CRD — and triggers backup jobs according to the schedule defined in the resource. Store backup state in etcd annotations."},
    {"id": "A025", "type": "build_request", "text": "So like I want to build, um, a tool that, uh, automatically generates API documentation from a Python codebase — reads the function signatures and docstrings and generates a nice HTML doc site. Think pdoc but customisable."},

    # ── Refactor/fix requests (20) ──
    {"id": "A026", "type": "refactor_fix", "text": "The authentication module is a mess — session management, JWT, OAuth all tangled together in one file. Refactor it into separate modules, add proper error handling, and write tests for each component."},
    {"id": "A027", "type": "refactor_fix", "text": "Our database queries are scattered throughout the codebase — in controllers, in route handlers, in helpers. Refactor to a proper repository pattern so all DB access goes through dedicated repository classes."},
    {"id": "A028", "type": "refactor_fix", "text": "The API is returning 200 status codes for errors — like when a user isn't found we return 200 with an error in the body. Fix all endpoints to use proper HTTP status codes — 404, 400, 401, 500 as appropriate."},
    {"id": "A029", "type": "refactor_fix", "text": "So the, um, front end component is like five hundred lines and it's doing way too much — data fetching, business logic, rendering, state management. Break it into smaller components and extract the logic into custom hooks."},
    {"id": "A030", "type": "refactor_fix", "text": "The Python script runs fine for small files but crashes with a memory error on files over 500MB. Refactor it to use streaming and process the file in chunks instead of loading it all into memory at once."},
    {"id": "A031", "type": "refactor_fix", "text": "There's a race condition in the queue processor — when two workers pick up the same job simultaneously. Fix it by implementing proper distributed locking using Redis, and add visibility timeout handling."},
    {"id": "A032", "type": "refactor_fix", "text": "The unit test suite takes eighteen minutes to run. It's mostly because of slow integration tests mixed in with unit tests. Separate them, mock external dependencies in unit tests, and set up a separate CI job for integration tests."},
    {"id": "A033", "type": "refactor_fix", "text": "Um, so the, uh, login is broken in production — works locally, not on the server. The error in the logs is about, like, a CORS issue with the cookie settings. Fix it and make sure you understand why it worked locally."},
    {"id": "A034", "type": "refactor_fix", "text": "The dashboard is slow — it loads in six seconds. Profile it and fix the top bottlenecks. I suspect it's doing too many sequential API calls and not caching anything. Fix both problems."},
    {"id": "A035", "type": "refactor_fix", "text": "The codebase has no TypeScript — everything is plain JavaScript with implicit any everywhere. Add TypeScript with strict mode, type all the function signatures, and set up the tsconfig properly."},
    {"id": "A036", "type": "refactor_fix", "text": "The error handling is inconsistent — some places throw, some return null, some return an error object. Standardise on a Result type pattern and update all the affected functions."},
    {"id": "A037", "type": "refactor_fix", "text": "So the, uh, build process is, like, broken after someone upgraded the webpack config. It's failing with some module resolution error. Fix the webpack config and while you're at it, upgrade to webpack 5 properly."},
    {"id": "A038", "type": "refactor_fix", "text": "The API has no rate limiting and someone is hammering it in production. Add rate limiting middleware — one hundred requests per minute per API key, proper 429 responses, and alerting when limits are hit."},
    {"id": "A039", "type": "refactor_fix", "text": "The mobile app leaks memory on the list view — memory grows every time the user scrolls. Profile it and fix the issue. I think it's related to images not being released but I'm not certain."},
    {"id": "A040", "type": "refactor_fix", "text": "Refactor the CSV export feature to handle large datasets without timing out. It currently loads everything into memory and generates the CSV synchronously. Move it to a background job with a download link sent when complete."},
    {"id": "A041", "type": "refactor_fix", "text": "So, um, the search feature is, like, doing a full table scan on every query and it's getting slower as the data grows. Add proper full-text search indexing — I think Postgres has this built in — and benchmark the improvement."},
    {"id": "A042", "type": "refactor_fix", "text": "The notification system sends duplicate notifications sometimes — when the server restarts during processing. Fix it with exactly-once processing semantics and add idempotency keys."},
    {"id": "A043", "type": "refactor_fix", "text": "The CI/CD pipeline takes forty minutes. Profile it and cut it in half. I think there are redundant steps, the Docker layers aren't cached properly, and tests could be parallelised."},
    {"id": "A044", "type": "refactor_fix", "text": "Upgrade the React app from class components to functional components with hooks throughout. There are about thirty components that need conversion. Write a brief migration guide for the pattern."},
    {"id": "A045", "type": "refactor_fix", "text": "The payment webhook handler has no retry logic and no idempotency checking. Fix it to handle retries correctly, deduplicate events using the event ID, and add dead letter queue handling."},

    # ── Research/compare requests (15) ──
    {"id": "A046", "type": "research_compare", "text": "Compare Postgres versus MySQL for a new SaaS app. I need to know which is better for complex queries, JSON support, replication, and how they differ on managed cloud offerings."},
    {"id": "A047", "type": "research_compare", "text": "Research the best options for real-time data syncing between a mobile app and server. Compare Firebase Realtime Database, Firestore, Supabase, and building with WebSockets and Postgres. Evaluate on cost, complexity, offline support."},
    {"id": "A048", "type": "research_compare", "text": "I need to choose between Kubernetes and ECS for our container orchestration. We're a team of eight engineers, AWS shop, about twenty microservices. Give me a clear recommendation with reasoning."},
    {"id": "A049", "type": "research_compare", "text": "Um, so research, like, the best Python web frameworks for building a, you know, high-performance API — FastAPI, Django REST framework, Flask, Litestar — compare on performance, ecosystem, async support, developer experience."},
    {"id": "A050", "type": "research_compare", "text": "Compare the main options for full-text search: Elasticsearch, Algolia, Typesense, and Postgres full-text search. We have about ten million records and need sub-100ms search latency."},
    {"id": "A051", "type": "research_compare", "text": "Research feature flagging solutions — LaunchDarkly, Flagsmith, Split.io, and building our own with Redis. Evaluate on cost, ease of integration, targeting capabilities, and analytics."},
    {"id": "A052", "type": "research_compare", "text": "I want to understand the tradeoffs between using a message queue — RabbitMQ, Kafka, SQS — versus a database-backed job queue like Sidekiq or BullMQ for our background processing needs."},
    {"id": "A053", "type": "research_compare", "text": "So, uh, research the, like, best options for, um, sending transactional emails — SendGrid, Postmark, AWS SES, Resend — compare deliverability, pricing at scale, API quality, and template management."},
    {"id": "A054", "type": "research_compare", "text": "Compare GraphQL versus REST versus tRPC for our new API. We're building a Next.js app with a TypeScript backend. Type safety and developer experience are important. Give a clear recommendation."},
    {"id": "A055", "type": "research_compare", "text": "Research the best approach to implementing multi-tenancy in a SaaS application. Compare: separate database per tenant, schema per tenant, shared database with tenant column. Evaluate on isolation, cost, complexity."},
    {"id": "A056", "type": "research_compare", "text": "I need to choose a monitoring and observability stack. Compare Datadog, New Relic, Grafana stack (Prometheus/Grafana/Loki), and AWS CloudWatch. Budget is roughly five hundred a month, we're AWS-based."},
    {"id": "A057", "type": "research_compare", "text": "Research AI coding assistants — GitHub Copilot, Cursor, Continue, Codeium — compare on code quality, IDE integration, privacy options, pricing, and whether they work well offline."},
    {"id": "A058", "type": "research_compare", "text": "So, like, compare the, uh, main options for, you know, managing secrets in production — AWS Secrets Manager, HashiCorp Vault, Doppler, and just using environment variables. Evaluate on security, cost, ease of use."},
    {"id": "A059", "type": "research_compare", "text": "Compare Vercel, Railway, Render, and Fly.io for deploying a Node.js API with a PostgreSQL database. Evaluate on pricing, performance, ease of use, and scaling capabilities."},
    {"id": "A060", "type": "research_compare", "text": "Research the best approaches to implementing real-time collaborative editing — like Google Docs — compare OT (operational transformation) versus CRDT approaches, and evaluate existing libraries like Yjs, Automerge, and ShareJS."},

    # ── Vague engineering ideas needing structuring (15) ──
    {"id": "A061", "type": "vague_idea", "text": "I want to do something with AI and our existing product. Like make it smarter. Maybe use GPT somehow. I don't know, something that would make users go wow."},
    {"id": "A062", "type": "vague_idea", "text": "We need to be better at monitoring. Like we don't really know what's happening in production. Something should tell us when things go wrong before users complain."},
    {"id": "A063", "type": "vague_idea", "text": "Um so I was thinking, like, what if the app could, you know, basically learn from how users use it and, like, get better over time. Like personalisation or something."},
    {"id": "A064", "type": "vague_idea", "text": "I want to build something that helps our team be more productive. Like maybe something that automates the boring stuff we do every day. I don't know what specifically yet."},
    {"id": "A065", "type": "vague_idea", "text": "We should make the app faster. Like it feels slow and I think we can do better. I want someone to look into it and figure out what the bottlenecks are."},
    {"id": "A066", "type": "vague_idea", "text": "So the idea is like a marketplace kind of thing. Where developers can share their — hmm — their tools? Or templates? Something like that. For our platform."},
    {"id": "A067", "type": "vague_idea", "text": "I want better analytics. Like right now I have no idea what users are actually doing in the app. I want to be able to see the paths they take, where they drop off, what they use most."},
    {"id": "A068", "type": "vague_idea", "text": "Um, like, we should do, you know, something about, uh, making the app work offline. Like if you're on a plane you should still be able to use it and then it syncs when you're back online."},
    {"id": "A069", "type": "vague_idea", "text": "I want to automate our deployment process. Right now it's all manual and it's slow and error-prone. Whatever good looks like, that's what I want."},
    {"id": "A070", "type": "vague_idea", "text": "Something about making the API easier to use for developers. Like it's a bit confusing now. Better docs maybe? Or a different design? I'm not sure."},
    {"id": "A071", "type": "vague_idea", "text": "I want to explore whether we can use machine learning to predict which users are going to churn. So we can reach out to them before they leave."},
    {"id": "A072", "type": "vague_idea", "text": "So basically a way for users to share things with each other. Like, inside the app. Collaboration I guess. But I haven't thought about what exactly."},
    {"id": "A073", "type": "vague_idea", "text": "I think we need to redesign the database. It's not really working well anymore. Like queries are slow and it's hard to add new features without breaking things."},
    {"id": "A074", "type": "vague_idea", "text": "Um, you know, like, an admin panel would be really useful. So we can see what users are doing and help them when they have issues. And manage accounts and stuff."},
    {"id": "A075", "type": "vague_idea", "text": "I want to make the mobile experience better. Like it works but it doesn't feel native. Lots of small things. It just needs polish I think."},

    # ── Multi-requirement complex builds (15) ──
    {"id": "A076", "type": "complex_build", "text": "Build a full SaaS authentication system with: email/password signup and login, email verification, password reset via email, Google OAuth, JWT tokens with refresh token rotation, rate limiting on login attempts, device sessions management showing active devices, and a user profile page. Backend in FastAPI, PostgreSQL, Redis for sessions."},
    {"id": "A077", "type": "complex_build", "text": "I want a complete e-commerce checkout flow — React frontend. Features: product selection, cart with quantity management, shipping address form with validation, multiple payment methods via Stripe including cards and PayPal, order confirmation email, order history page, and admin view of all orders. Mobile responsive."},
    {"id": "A078", "type": "complex_build", "text": "Build an API gateway service that handles: authentication via JWT, rate limiting per user and per endpoint, request logging to CloudWatch, response caching in Redis, circuit breaker pattern for downstream services, automatic retry with backoff, and a health dashboard showing real-time request stats."},
    {"id": "A079", "type": "complex_build", "text": "Um, so I need like a, uh, complete notification system. So it needs to support, you know, multiple channels — email, SMS via Twilio, push notifications, in-app. Template management, user preferences for which channels they want, delivery tracking, retry logic for failed sends, and analytics on open rates and click rates."},
    {"id": "A080", "type": "complex_build", "text": "Create a document processing pipeline: upload PDFs and Word docs, extract text using OCR if needed, chunk the text, generate embeddings with OpenAI, store in a vector database — pgvector — and expose a semantic search API. Add a simple web UI to upload documents and search them."},
    {"id": "A081", "type": "complex_build", "text": "Build a team collaboration feature for our existing app. Users can create workspaces, invite team members via email, assign roles — owner, admin, member, viewer — share resources within the workspace, see activity feeds, and workspace admins can manage billing and member access."},
    {"id": "A082", "type": "complex_build", "text": "I need a data import system that accepts CSV and Excel files, validates them against a configurable schema, shows a preview with validation errors highlighted, allows column mapping where the file columns don't match expected columns, processes in background with progress indicator, and sends completion notification."},
    {"id": "A083", "type": "complex_build", "text": "Build a comprehensive audit logging system: capture every user action across the app — what they did, what changed, before and after values — searchable audit log UI with filters by user, date, action type, export to CSV, retention policy — keep for ninety days — and alerting for suspicious patterns like bulk deletes."},
    {"id": "A084", "type": "complex_build", "text": "Create a public API with full developer tooling: RESTful API with versioning, API key management — create, revoke, usage stats — auto-generated OpenAPI docs with Swagger UI, SDKs in Python and JavaScript auto-generated from the spec, a sandbox environment with test credentials, and a developer portal showing keys and usage."},
    {"id": "A085", "type": "complex_build", "text": "So, like, build a, uh, real-time analytics dashboard. Should, um, show: live event count, user activity heatmap by hour and day, funnel analysis for key flows, cohort retention chart, revenue metrics — MRR, ARR, churn — and custom metric builder where non-technical people can define their own charts. Data via Kafka stream, Clickhouse for storage, React frontend, websockets for live updates."},
    {"id": "A086", "type": "complex_build", "text": "Build a workflow automation engine — like a simple Zapier for internal use. Users define triggers — new database row, API call, scheduled time — and actions — send email, call webhook, update database, send Slack message. Visual builder with a drag-and-drop UI. Execution history with logs. Retry on failure."},
    {"id": "A087", "type": "complex_build", "text": "Create a complete CI/CD pipeline setup for a Node.js monorepo with multiple services. GitHub Actions for CI: linting, testing, security scanning, Docker build and push. CD: staging auto-deploy on merge to main, production deploy with manual approval, blue-green deployment, automatic rollback on error rate spike, Slack notifications for deploys."},
    {"id": "A088", "type": "complex_build", "text": "Build a customer support ticketing system: users submit tickets via web form or email, auto-routing by category using keyword matching, agent assignment, priority levels, SLA tracking with breach alerts, internal notes, email notifications to user at each status change, reporting dashboard with response times and volumes."},
    {"id": "A089", "type": "complex_build", "text": "I want a subscription billing system integrated with Stripe. Features: multiple plan tiers, monthly and annual billing, proration when users upgrade or downgrade, trial periods, coupon codes and discounts, dunning management for failed payments with automatic retry and escalating emails, revenue recognition reports, and a customer billing portal."},
    {"id": "A090", "type": "complex_build", "text": "Build a multi-region deployment setup for our API. The API should run in US East, EU West, and Asia Pacific. Global load balancing with latency-based routing. Database: primary in US with read replicas in each region. Cache invalidation that works across regions. Monitoring per region with aggregated view. Terraform for all infrastructure."},

    # ── Architecture/design decisions (10) ──
    {"id": "A091", "type": "architecture", "text": "We're at the point where the monolith needs to be broken up but I'm not sure whether to go full microservices or a modular monolith. We have twelve engineers, high deployment frequency, and about twenty distinct business domains. What's the right approach?"},
    {"id": "A092", "type": "architecture", "text": "I need to decide whether to store user-generated media — images, videos, documents — in our own S3 bucket and serve it ourselves, or use a CDN like Cloudflare or use a managed service like Cloudinary. We expect about one million files in year one."},
    {"id": "A093", "type": "architecture", "text": "Um, so the question is, like, should we, uh, use a single database for everything or, you know, separate databases for different services. We have a user service, a billing service, an analytics service, and a core product service."},
    {"id": "A094", "type": "architecture", "text": "I'm designing the caching strategy for our API. Should I cache at the CDN layer, the application layer with Redis, the database query layer, or some combination? The API has both high-frequency reads and writes."},
    {"id": "A095", "type": "architecture", "text": "We need to design the data model for a multi-tenant SaaS. The main entities are: tenants, users (many per tenant), projects (many per tenant), items (many per project), and comments (many per item). Design this for efficient querying and row-level security."},
    {"id": "A096", "type": "architecture", "text": "Should we build our backend as a REST API, a GraphQL API, or an RPC-style API? We have three clients: a web app, a mobile app, and third-party developers who'll integrate with us. Type safety matters a lot to our team."},
    {"id": "A097", "type": "architecture", "text": "Um, so the, uh, question is about, like, event sourcing versus traditional CRUD for our, you know, core domain. We have an audit requirement and undo functionality needs to be added. Is event sourcing overkill for us?"},
    {"id": "A098", "type": "architecture", "text": "I need to design the authentication architecture for a product that has a web app, mobile app, and public API. The requirements are: SSO support, MFA, short-lived access tokens, long-lived refresh tokens, device management, and the ability to revoke access instantly."},
    {"id": "A099", "type": "architecture", "text": "We're building a system that needs to process about one hundred thousand events per second at peak. I need to decide between a traditional message queue approach — Kafka, SQS — versus a stream processing approach — Flink, Spark Streaming. Help me think through the right choice."},
    {"id": "A100", "type": "architecture", "text": "Design the file processing architecture for a service that accepts large file uploads — up to 10GB — processes them asynchronously, and the processing can take anywhere from thirty seconds to four hours. Needs progress reporting, resumable uploads, and the ability to cancel in-progress jobs."},
]


def call_gpt(system_prompt, user_message, model="gpt-4o-mini", max_tokens=800):
    """Call GPT API with retry logic."""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=max_tokens,
                temperature=0.0,
            )
            return resp.choices[0].message.content
        except Exception as e:
            if attempt == 2:
                raise
            print(f"    [retry {attempt+1}] {e}")
            time.sleep(2 ** attempt)


def run_transcript_through_prompt(prompt_template, transcript_text):
    """Run a transcript through the prompt, substituting {transcript}."""
    filled_prompt = prompt_template.replace("{transcript}", transcript_text)
    return call_gpt(filled_prompt, "")


def score_output(transcript_text, output_text, transcript_type, mode):
    """Score the output 0-100 using GPT as judge."""
    if transcript_type == "noise" and output_text.strip() == "":
        return 100, "Noise correctly returned empty"

    scoring_prompt = f"""You are evaluating the quality of a voice-to-text formatter's output.

Mode: {mode}
Transcript type: {transcript_type}

Original transcript:
{transcript_text}

Formatted output:
{output_text}

Score the output on these criteria (total 100 points):

1. Accuracy (30pts): Did it capture everything meaningful from the transcript?
   - 0: Major content missing
   - 15: Some content missing
   - 25: Minor omissions only  
   - 30: Everything captured

2. Format (25pts): Is the format right for the content type?
   - Lists should be bullet lists
   - Tasks should be single clear sentences
   - Messages should be prose
   - Notes should be organised bullet points
   - Noise should return empty string
   - 0: Completely wrong format
   - 10: Partially right format
   - 20: Mostly right format
   - 25: Perfect format for content type

3. Cleanliness (20pts): Are all filler words removed?
   - Filler: um, uh, like, you know, so, basically, literally, kind of, sort of
   - 0: Many fillers remain
   - 10: Some fillers remain
   - 16: Very few fillers remain
   - 20: Completely clean

4. Self-corrections (15pts): If speaker corrected themselves, is the FINAL version used?
   - If no corrections: full 15pts
   - 0: Used the wrong version
   - 8: Partially handled
   - 15: Correctly used final version

5. No hallucinations (10pts): Did it add things not said?
   - 0: Added significant content
   - 5: Added minor content
   - 10: Nothing added

Special rules:
- If transcript is noise/garbage/testing and output is empty: give 100/100
- If transcript is noise/garbage and output is NOT empty: give 0/100

Respond in this exact JSON format:
{{"accuracy": N, "format": N, "cleanliness": N, "self_corrections": N, "no_hallucinations": N, "total": N, "notes": "brief explanation"}}"""

    result = call_gpt(scoring_prompt, "", max_tokens=300)
    try:
        # Extract JSON from response
        import re
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            scores = json.loads(json_match.group())
            return scores["total"], scores.get("notes", "")
    except Exception as e:
        print(f"    [score parse error] {e}: {result[:100]}")
    return 50, "Parse error"


def run_mode(mode_name, transcripts, prompt, prompt_label):
    """Run all transcripts for a mode, return results."""
    results = []
    print(f"\n{'='*60}")
    print(f"Running {mode_name} mode ({len(transcripts)} transcripts)...")
    print(f"{'='*60}")

    for i, t in enumerate(transcripts):
        tid = t["id"]
        ttype = t["type"]
        text = t["text"]

        print(f"  [{i+1:3d}/{len(transcripts)}] {tid} ({ttype[:20]:<20})", end="", flush=True)

        # Generate output
        try:
            output = run_transcript_through_prompt(prompt, text)
        except Exception as e:
            output = f"[ERROR: {e}]"
            print(f" ERROR: {e}")
            results.append({
                "id": tid, "type": ttype, "transcript": text,
                "output": output, "score": 0, "score_notes": str(e),
                "mode": mode_name
            })
            continue

        # Score
        try:
            score, notes = score_output(text, output, ttype, mode_name)
        except Exception as e:
            score, notes = 50, f"Score error: {e}"

        print(f" score={score:3d}  {notes[:50]}")

        results.append({
            "id": tid,
            "type": ttype,
            "transcript": text,
            "output": output,
            "score": score,
            "score_notes": notes,
            "mode": mode_name
        })

        # Small delay to avoid rate limits
        time.sleep(0.3)

    return results


def improve_prompt_smart(prompt, worst_cases):
    """Generate an improved smart/normal prompt based on worst cases."""
    cases_text = "\n\n".join([
        f"Case {c['id']} (type={c['type']}, score={c['score']}):\nTranscript: {c['transcript'][:200]}\nOutput: {c['output'][:200]}\nIssue: {c['score_notes']}"
        for c in worst_cases
    ])

    improve_prompt = f"""You are an expert prompt engineer. Here is the current system prompt for a voice-to-text formatter:

<current_prompt>
{prompt}
</current_prompt>

These are the {len(worst_cases)} worst-performing cases:

{cases_text}

Analyse the failure patterns and rewrite the prompt to fix them. The prompt must:
1. Fix the identified failure patterns without breaking what works
2. Keep all the existing good instructions
3. Add clarity where the model was confused
4. Be concise (don't make it much longer)
5. Keep the same structure and style

Output ONLY the improved prompt text. Nothing else. No preamble. No "here is the improved prompt:"."""

    return call_gpt(improve_prompt, "", model="gpt-4o-mini", max_tokens=2000)


def improve_prompt_adhd(prompt, worst_cases):
    """Generate an improved ADHD ramble prompt based on worst cases."""
    cases_text = "\n\n".join([
        f"Case {c['id']} (type={c['type']}, score={c['score']}):\nTranscript: {c['transcript'][:300]}\nOutput: {c['output'][:300]}\nIssue: {c['score_notes']}"
        for c in worst_cases
    ])

    improve_prompt = f"""You are an expert prompt engineer. Here is the current system prompt for an ADHD ramble voice formatter:

<current_prompt>
{prompt}
</current_prompt>

These are the {len(worst_cases)} worst-performing cases:

{cases_text}

Analyse the failure patterns and rewrite the prompt to fix them. Key failure modes to watch for:
- Not capturing all topics in multi-topic rambles
- Losing self-corrections (using old version instead of final version)
- Over-structuring very simple inputs
- Under-structuring complex multi-topic dumps
- Adding markdown headers when not needed

Output ONLY the improved prompt text. Nothing else."""

    return call_gpt(improve_prompt, "", model="gpt-4o-mini", max_tokens=3000)


def improve_prompt_agentic(prompt, worst_cases):
    """Generate an improved agentic engineering prompt based on worst cases."""
    cases_text = "\n\n".join([
        f"Case {c['id']} (type={c['type']}, score={c['score']}):\nTranscript: {c['transcript'][:300]}\nOutput: {c['output'][:300]}\nIssue: {c['score_notes']}"
        for c in worst_cases
    ])

    improve_prompt = f"""You are an expert prompt engineer. Here is the current system prompt for an agentic engineer voice formatter:

<current_prompt>
{prompt}
</current_prompt>

These are the {len(worst_cases)} worst-performing cases:

{cases_text}

Analyse the failure patterns and rewrite the prompt to fix them. Key failure modes to watch for:
- Missing tech stack details
- Dropping requirements mentioned mid-ramble
- Wrong format used (e.g. brain dump format for a build request)
- Vague outputs that aren't ready to paste into an AI coding agent
- Not structuring vague ideas into actionable prompts

Output ONLY the improved prompt text. Nothing else."""

    return call_gpt(improve_prompt, "", model="gpt-4o-mini", max_tokens=3000)


def verify_improvement(worst_cases, original_prompt, new_prompt, mode_name):
    """Run worst cases through new prompt and compare scores."""
    print(f"\n  Verifying improvement for {mode_name}...")
    before_scores = []
    after_scores = []

    for c in worst_cases:
        print(f"    {c['id']} (was {c['score']})", end="", flush=True)
        try:
            new_output = run_transcript_through_prompt(new_prompt, c["transcript"])
            new_score, new_notes = score_output(c["transcript"], new_output, c["type"], mode_name)
        except Exception as e:
            new_score, new_notes = c["score"], f"Error: {e}"

        before_scores.append(c["score"])
        after_scores.append(new_score)
        print(f" → {new_score} ({'+' if new_score >= c['score'] else ''}{new_score - c['score']})")
        time.sleep(0.3)

    avg_before = sum(before_scores) / len(before_scores)
    avg_after = sum(after_scores) / len(after_scores)
    print(f"  Average: {avg_before:.1f} → {avg_after:.1f} ({'+' if avg_after >= avg_before else ''}{avg_after - avg_before:.1f})")

    return avg_before, avg_after


def main():
    print("=" * 70)
    print("Waffler Deep Prompt Quality Testing")
    print("=" * 70)

    # Load prompts
    smart_prompt = load_prompt("smart.txt")
    adhd_prompt = load_prompt("adhd_ramble.txt")
    agentic_prompt = load_prompt("agentic_engineering.txt")

    all_results = {}

    # ── Run Normal mode ──
    normal_results = run_mode("Normal/Smart", NORMAL_TRANSCRIPTS, smart_prompt, "smart.txt")
    all_results["normal"] = normal_results

    # ── Run Ramble mode ──
    ramble_results = run_mode("Ramble/ADHD", RAMBLE_TRANSCRIPTS, adhd_prompt, "adhd_ramble.txt")
    all_results["ramble"] = ramble_results

    # ── Run Agentic mode ──
    agentic_results = run_mode("Agentic Engineer", AGENTIC_TRANSCRIPTS, agentic_prompt, "agentic_engineering.txt")
    all_results["agentic"] = agentic_results

    # ── Calculate stats ──
    print("\n\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    mode_stats = {}
    for mode_key, results in all_results.items():
        scores = [r["score"] for r in results]
        avg = sum(scores) / len(scores)
        worst5 = sorted(results, key=lambda x: x["score"])[:5]
        mode_stats[mode_key] = {"avg": avg, "worst5": worst5, "scores": scores}

        print(f"\n{mode_key.upper()} MODE:")
        print(f"  Average score: {avg:.1f}/100")
        print(f"  Min: {min(scores)}, Max: {max(scores)}")
        print(f"  Top 5 worst:")
        for w in worst5:
            print(f"    {w['id']} ({w['type']}) score={w['score']}: {w['score_notes'][:60]}")

    # ── Improve prompts ──
    print("\n\n" + "=" * 70)
    print("IMPROVING PROMPTS")
    print("=" * 70)

    improved_prompts = {}

    # Normal/Smart
    worst_normal_20 = sorted(all_results["normal"], key=lambda x: x["score"])[:20]
    print(f"\nImproving Normal/Smart prompt (based on {len(worst_normal_20)} worst cases)...")
    improved_smart = improve_prompt_smart(smart_prompt, worst_normal_20)
    improved_prompts["smart"] = improved_smart

    # ADHD Ramble
    worst_ramble_20 = sorted(all_results["ramble"], key=lambda x: x["score"])[:20]
    print(f"Improving ADHD Ramble prompt (based on {len(worst_ramble_20)} worst cases)...")
    improved_adhd = improve_prompt_adhd(adhd_prompt, worst_ramble_20)
    improved_prompts["adhd"] = improved_adhd

    # Agentic
    worst_agentic_20 = sorted(all_results["agentic"], key=lambda x: x["score"])[:20]
    print(f"Improving Agentic Engineering prompt (based on {len(worst_agentic_20)} worst cases)...")
    improved_agentic = improve_prompt_agentic(agentic_prompt, worst_agentic_20)
    improved_prompts["agentic"] = improved_agentic

    # ── Verify improvements
    print("\n\n" + "=" * 70)
    print("VERIFYING IMPROVEMENTS")
    print("=" * 70)

    verification_results = {}

    # Verify Normal
    before_n, after_n = verify_improvement(worst_normal_20, smart_prompt, improved_smart, "Normal/Smart")
    verification_results["normal"] = {"before": before_n, "after": after_n, "improved": after_n > before_n + 1}

    # Verify Ramble
    before_r, after_r = verify_improvement(worst_ramble_20, adhd_prompt, improved_adhd, "Ramble/ADHD")
    verification_results["ramble"] = {"before": before_r, "after": after_r, "improved": after_r > before_r + 1}

    # Verify Agentic
    before_a, after_a = verify_improvement(worst_agentic_20, agentic_prompt, improved_agentic, "Agentic Engineer")
    verification_results["agentic"] = {"before": before_a, "after": after_a, "improved": after_a > before_a + 1}

    # ── Save improved prompts if better ──
    print("\n\n" + "=" * 70)
    print("SAVING RESULTS")
    print("=" * 70)

    # Always save v3 versions
    with open(os.path.join(PROMPTS_PATH, "smart_v3.txt"), "w") as f:
        f.write(improved_smart)
    print("Saved prompts/smart_v3.txt")

    with open(os.path.join(PROMPTS_PATH, "adhd_ramble_v3.txt"), "w") as f:
        f.write(improved_adhd)
    print("Saved prompts/adhd_ramble_v3.txt")

    with open(os.path.join(PROMPTS_PATH, "agentic_engineering_v3.txt"), "w") as f:
        f.write(improved_agentic)
    print("Saved prompts/agentic_engineering_v3.txt")

    # Copy over originals if improvement > 1 point
    for mode_key, vr in verification_results.items():
        if vr["improved"]:
            if mode_key == "normal":
                with open(os.path.join(PROMPTS_PATH, "smart.txt"), "w") as f:
                    f.write(improved_prompts["smart"])
                print(f"✓ Copied improved smart.txt (avg {vr['before']:.1f} → {vr['after']:.1f})")
            elif mode_key == "ramble":
                with open(os.path.join(PROMPTS_PATH, "adhd_ramble.txt"), "w") as f:
                    f.write(improved_prompts["adhd"])
                print(f"✓ Copied improved adhd_ramble.txt (avg {vr['before']:.1f} → {vr['after']:.1f})")
            elif mode_key == "agentic":
                with open(os.path.join(PROMPTS_PATH, "agentic_engineering.txt"), "w") as f:
                    f.write(improved_prompts["agentic"])
                print(f"✓ Copied improved agentic_engineering.txt (avg {vr['before']:.1f} → {vr['after']:.1f})")
        else:
            print(f"  {mode_key}: No significant improvement ({vr['before']:.1f} → {vr['after']:.1f}), keeping original")

    # ── Save full results JSON ──
    output = {
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "normal": {
                "avg_score": mode_stats["normal"]["avg"],
                "n_transcripts": len(all_results["normal"]),
                "worst_5": [
                    {"id": w["id"], "type": w["type"], "score": w["score"],
                     "transcript": w["transcript"][:150], "output": w["output"][:150],
                     "notes": w["score_notes"]}
                    for w in mode_stats["normal"]["worst5"]
                ]
            },
            "ramble": {
                "avg_score": mode_stats["ramble"]["avg"],
                "n_transcripts": len(all_results["ramble"]),
                "worst_5": [
                    {"id": w["id"], "type": w["type"], "score": w["score"],
                     "transcript": w["transcript"][:150], "output": w["output"][:150],
                     "notes": w["score_notes"]}
                    for w in mode_stats["ramble"]["worst5"]
                ]
            },
            "agentic": {
                "avg_score": mode_stats["agentic"]["avg"],
                "n_transcripts": len(all_results["agentic"]),
                "worst_5": [
                    {"id": w["id"], "type": w["type"], "score": w["score"],
                     "transcript": w["transcript"][:150], "output": w["output"][:150],
                     "notes": w["score_notes"]}
                    for w in mode_stats["agentic"]["worst5"]
                ]
            }
        },
        "improvements": {
            "normal": {
                "prompt_v3_saved": True,
                "copied_to_original": verification_results["normal"]["improved"],
                "worst_20_avg_before": verification_results["normal"]["before"],
                "worst_20_avg_after": verification_results["normal"]["after"],
            },
            "ramble": {
                "prompt_v3_saved": True,
                "copied_to_original": verification_results["ramble"]["improved"],
                "worst_20_avg_before": verification_results["ramble"]["before"],
                "worst_20_avg_after": verification_results["ramble"]["after"],
            },
            "agentic": {
                "prompt_v3_saved": True,
                "copied_to_original": verification_results["agentic"]["improved"],
                "worst_20_avg_before": verification_results["agentic"]["before"],
                "worst_20_avg_after": verification_results["agentic"]["after"],
            }
        },
        "improved_prompts": {
            "smart_v3": improved_smart,
            "adhd_ramble_v3": improved_adhd,
            "agentic_engineering_v3": improved_agentic,
        },
        "all_results": all_results
    }

    results_path = os.path.join(PROJECT_PATH, "test_results_deep.json")
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull results saved to: {results_path}")

    # ── Final report ──
    print("\n\n" + "=" * 70)
    print("FINAL REPORT")
    print("=" * 70)
    for mode_key, stats in mode_stats.items():
        print(f"\n{'─'*40}")
        print(f"{mode_key.upper()} MODE — avg {stats['avg']:.1f}/100")
        print(f"{'─'*40}")
        print("Top 5 worst cases:")
        for w in stats["worst5"]:
            print(f"  {w['id']} [{w['type']}] score={w['score']}")
            print(f"    Transcript: {w['transcript'][:80]}...")
            print(f"    Output:     {w['output'][:80]}...")
            print(f"    Issue:      {w['score_notes'][:80]}")

    print("\n\nPROMPT IMPROVEMENT SUMMARY:")
    for mode_key, vr in verification_results.items():
        delta = vr["after"] - vr["before"]
        status = "✓ IMPROVED & SAVED" if vr["improved"] else "✗ not saved"
        print(f"  {mode_key}: {vr['before']:.1f} → {vr['after']:.1f} ({delta:+.1f}) [{status}]")

    print("\n✅ Deep testing complete!")


if __name__ == "__main__":
    main()
