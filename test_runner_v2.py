#!/usr/bin/env python3
"""
Waffler v10 - Deep Transcript Testing
Tests all 3 modes with 100 synthetic transcripts each, scores, improves prompts.
"""

import json
import os
import re
import time
from openai import OpenAI

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
PROJECT_DIR = "/Users/tars/Desktop/waffler"
RESULTS_FILE = os.path.join(PROJECT_DIR, "test_results_v2.json")

client = OpenAI(api_key=OPENAI_KEY)

JAMES_REAL_TRANSCRIPT = "yes set up the synthetic transcript testing idea and then rate the responses out of 100 and improve based on them spawn multiple sub agents to parallelise it and then save the results to a json file"

# ─────────────────────────────────────────────
# TRANSCRIPT GENERATORS
# ─────────────────────────────────────────────

SMART_TRANSCRIPTS = [
    # LIST types
    "um I need to pick up bananas milk cereal and like maybe some yogurt",
    "groceries for the week so first eggs then bread uh orange juice and coffee",
    "shopping list um apples pears grapes and some like frozen meals",
    "things to do today first call mum then uh email the accountant and also book a dentist",
    "my top priorities are basically finish the report um review the budget and send the proposal",
    "pack for the trip so passport charger um headphones and like my laptop obviously",
    "ingredients I need flour sugar butter eggs and vanilla extract",
    "the team needs to work on the frontend the backend and also the database design",
    "three main risks are cost overrun timeline delays and uh vendor lock-in",
    "books I want to read atomic habits deep work and sort of the lean startup",

    # MESSAGE types
    "hey sarah just wanted to let you know that uh the meeting has been moved to Thursday at 3pm hope that works",
    "hi can you send me the report by end of day today thanks so much",
    "email to john hi john just following up on our conversation last week um wanted to check if you had a chance to review the proposal",
    "message to the team basically good news everyone the project has been approved and we can start next Monday",
    "dear mr smith I am writing to um express my interest in the position advertised on your website",
    "text to mum hey mum just got to the airport safely boarding in like 20 minutes",
    "slack message to dave hey dave can you um review my PR when you get a chance no rush",
    "email subject line important update regarding the Q3 targets",
    "reply to client thanks for your patience we've resolved the issue and everything should be working now",
    "message to landlord hi I wanted to report a um broken heater in the flat could someone come take a look",

    # COMMAND/TASK types
    "create a landing page for my new app it should have a hero section pricing and contact form",
    "build a python script that reads CSV files and converts them to JSON",
    "make a logo for a coffee shop called morning brew something warm and inviting",
    "write a blog post about the benefits of remote work around 800 words",
    "design a database schema for a user management system with roles and permissions",
    "create a REST API endpoint that accepts POST requests and stores data in postgres",
    "build a chrome extension that highlights all emails from a specific domain",
    "write unit tests for the payment module in our react app",
    "make a Figma mockup for the checkout flow three screens please",
    "create a weekly email newsletter template for our subscribers",

    # NOTES types  
    "idea for the app um so users could set goals and then like track progress daily and get reminders",
    "meeting notes so we discussed the Q4 roadmap basically the main decision was to prioritize mobile",
    "thoughts on the pitch um we need a stronger value proposition and the slides need work on the financial section",
    "brainstorm for the campaign first social media ads then influencer outreach and maybe like a podcast sponsorship",
    "research notes on competitors um competitor A has better pricing competitor B has better UX",
    "book summary key ideas include habit stacking um the two-minute rule and environment design",
    "therapy session thoughts basically I've been feeling overwhelmed by work and need better boundaries",
    "travel planning so Lisbon in April flights hotel and um three restaurants I want to try",
    "product idea what if there was an app that um automatically categorizes your spending and suggests savings",
    "architecture notes the system should use microservices with like event-driven communication",

    # PROSE types
    "I think the most important thing about leadership is basically listening to your team and creating a safe environment",
    "the project is going well overall but um there are some challenges with the timeline that we need to address",
    "I've been thinking about switching careers and um I'm considering going into data science which requires some upskilling",
    "the restaurant was amazing the food was like incredible and the service was really attentive",
    "so basically machine learning models need lots of data to train and the quality of that data matters a lot",
    "I believe the economy is heading for a slowdown based on the indicators I've been looking at",
    "the conference was really informative and I learned a lot about new trends in AI and automation",
    "working from home has its advantages like flexibility but it also makes it harder to separate work and personal life",
    "the new iPhone has some interesting features but I'm not sure if it justifies the upgrade from my current phone",
    "climate change is one of the most pressing issues of our time and we need collective action to address it",

    # AGENTIC types
    "hey TARS I want you to analyze my codebase and find all the places where we're not handling errors properly",
    "search for all papers published in 2024 about large language model reasoning and summarize the top 5",
    "look at my calendar for next week and identify any conflicts or back-to-back meetings over an hour",
    "pull the latest sales data from the spreadsheet and create a summary report with key trends",
    "monitor the GitHub repo and notify me if any PR has been open for more than 3 days without review",

    # SELF-CORRECTIONS
    "I want to buy a blue um wait no a red car actually a blue one yes blue definitely",
    "the meeting is on Tuesday no wait Wednesday um actually let me check it's Thursday at 2pm",
    "send this to sarah actually no send it to the whole team",
    "I need 50 units no wait 500 units for the order",
    "the budget is 10k actually no it's 15000 dollars for the project",

    # FILLER-HEAVY
    "so basically um like I kind of want to you know start a podcast about like technology and stuff",
    "um like you know it's basically just like a simple app that like sort of tracks your like daily habits you know",
    "so I was thinking um like maybe we could you know sort of redesign the homepage to be like more modern",
    "literally um so basically I need like a report on you know the financial performance of Q3",
    "um so like the thing is basically our app kind of needs like better onboarding you know what I mean",

    # NOISE/EDGE CASES
    "um",
    "testing testing 1 2 3",
    "",
    "hello is this thing on",
    "uh uh uh uh",

    # MIXED CONTENT
    "okay so groceries are bananas and milk but also I need to email Tom about the project deadline which is Friday",
    "build the app and also remember to book flights for next month",
    "I need coffee but also create a new GitHub repo called voice-app and initialize it with a README",
    "shopping list eggs bread but actually this is more important create a Jira ticket for the bug in production",
    "call dentist but first build an API that does user authentication with JWT tokens",

    # NUMBERS/DATES/NAMES
    "the Q4 targets are 2.5 million in revenue with a 15 percent growth rate by December 31st",
    "contact Dr. Jennifer Walsh at 0207 456 7890 for the appointment on March 15th",
    "the React version should be 18.2 and we need Node 20 LTS",
    "the price is £49.99 per month with a 30-day free trial",
    "our NPS score dropped from 67 to 52 between January and February",

    # TECHNICAL
    "implement a Redis cache layer with a TTL of 3600 seconds for the user session data",
    "the API should rate limit to 100 requests per minute per IP using a sliding window algorithm",
    "we need to migrate from MySQL to PostgreSQL while maintaining backward compatibility",
    "set up a CI/CD pipeline with GitHub Actions deploying to AWS ECS on main branch merge",
    "implement WebSocket support for real-time notifications with fallback to long polling",

    # EMOTIONAL/URGENT
    "this is urgent the production server is down and users can't log in fix it now",
    "I'm so frustrated the client keeps changing requirements and I need help documenting all the changes",
    "amazing news we just got Series A funding of 5 million dollars announce it to the team",
    "I'm worried about the deadline can you help me prioritize what to cut from scope",
    "the demo is in 2 hours and nothing is working I need a plan right now",

    # PROFESSIONAL MESSAGES
    "to all staff regarding the office closure next Friday December 22nd the office will be closed for the holiday please plan accordingly",
    "follow up from our call today as discussed we'll proceed with option B and I'll send the contract by Thursday",
    "just a heads up there's a fire drill scheduled for tomorrow at 11am please make note",
    "quarterly review reminder please complete your self-assessments by the end of this week",
    "welcome to the team excited to have everyone on board let's schedule intros this week",

    # LISTS WITH SELF-CORRECTION
    "I need apples actually scratch that pears and um bananas and some like milk",
    "todo list first thing finish the presentation wait no first review the emails then finish the presentation",
    "things to pack um laptop charger actually make that two chargers headphones and um passport",

    # CREATIVE
    "write a short poem about autumn leaves falling in a city",
    "create a tagline for a meditation app that's fun and approachable not too spiritual",
    "come up with 5 names for a startup that does AI-powered scheduling",
    "describe the product in one sentence for a teenager",
    "write the opening line for a thriller novel set in 1920s Chicago",

    # AMBIGUOUS/TRICKY  
    "um so yeah that thing we talked about make it happen",
    "send that file to that person from yesterday",
    "change the thing in the app that's not working",
    "more blue less green in the design",
    "make it better and faster",

    # MULTILINGUAL hints
    "the project is called proyecto verde and the client is in Madrid expecting delivery by Lunes",
    "the file should be named rapport_final and sent to the équipe marketing",

    # STRUCTURED REQUESTS
    "create a project plan with milestones for a 3-month website redesign include design development testing and launch phases",
    "write a performance review template with sections for achievements challenges and goals for next quarter",
    "draft an agenda for tomorrow's all-hands meeting topics are Q4 results product roadmap and team announcements",
    "create a risk register for the infrastructure migration project",
    "write a job description for a senior full-stack developer role at a fintech startup",
]

RAMBLE_TRANSCRIPTS = [
    # Pure stream of consciousness - various lengths and complexity
    "okay so I've been thinking about this idea right um it started when I was in the shower this morning and I thought what if we just like completely rethink how the app works because right now it's kind of linear you go from A to B to C but users don't think that way they jump around they have multiple things on their mind at once so what if the app kind of mirrors that you know like a mind map kind of thing but also like practical tasks not just like visual bubbles but like actual things you can do and check off and maybe there's an AI layer that like helps you organize stuff as you add it kind of like what we're doing now actually wait that's kind of meta",
    
    "so the team meeting was productive I think well actually it started slow because Jake was late again and then Sarah had connection issues so we basically lost the first 20 minutes but once we got going the main thing we decided was to move the launch from March to April no wait we said April 15th specifically because that gives us time to do proper QA and also the marketing team needs at least two weeks for the campaign and Tom was saying actually Tom's point was interesting he thinks we should do a soft launch first like just to 100 users get feedback and then go wide and I kind of agree but the investors want a big splash you know",
    
    "I need to figure out what I'm doing with my career honestly like I've been at this company for 4 years and I like it but I'm not growing and I keep seeing these job postings for senior roles that I'm probably qualified for but I'm scared of interviewing because it's been so long and also I kind of feel loyal to my team even though the company kind of doesn't deserve that loyalty based on how they handled the layoffs last year anyway the point is I need to either push for a promotion here like actually have that conversation with my manager or start applying externally I can't keep just thinking about it",
    
    "so the bug report came in and basically users on iOS 16 are seeing a blank screen after login specifically iPhone 11 and 12 models but it works fine on 13 and above and Android is completely fine so it's something specific to those older iOS versions and WebKit rendering probably related to the CSS grid thing we changed last sprint actually wait no we changed the flex layout not grid anyway the point is we need to reproduce it first I've asked the QA team to get an iPhone 11 from the device lab and also can we check the Sentry logs for any JS errors around the auth flow",
    
    "ideas for the birthday party um so I was thinking like a garden theme but it's December so that doesn't work okay what about like a cosy winter thing so fairy lights and hot chocolate station and maybe one of those photo booth things and for food we could do finger sandwiches and those little pastry things and also like a cheese board I think that's classy and fun and oh music is important maybe a playlist or we could hire that guy Dave who does acoustic covers he's good but might be expensive and for venue we could do it at my place if we move the furniture around or the function room at the pub but that costs 200 pounds",
    
    "the API documentation is terrible and it's causing so much confusion for the integration partners basically there are three main problems first the authentication flow isn't clearly explained there are like 4 different endpoints for auth and it's not obvious which one to use second the error codes aren't documented at all partners are getting 422 errors with no explanation third the rate limits aren't published anywhere so people are just hitting them and getting confused what we need to do is write proper docs with examples create a quickstart guide that gets you from zero to first API call in under 10 minutes and set up a developer portal where partners can test the API interactively",
    
    "I'm trying to learn machine learning and I feel like I should have a structured plan so first I need to actually understand the math which means linear algebra and statistics then like the fundamentals of neural networks not just copying pytorch code but actually understanding what's happening then I want to do some real projects something with real data not just MNIST and I'm thinking maybe computer vision because that feels more tangible than NLP but actually NLP is more useful for what I want to do eventually which is building stuff with language models but maybe I should just pick one and go deep rather than trying to do everything",
    
    "random thoughts from today's commute so I was listening to that podcast about founders and the guest was talking about how the best companies are built around a hair-on-fire problem meaning the customer is so desperate for a solution they'll use anything even a bad product and that made me think about our app and whether we're really solving a hair-on-fire problem or just a nice-to-have because nice-to-have is dangerous because the moment something else grabs attention people forget you and also the guest made a point about pricing that you should charge more than you think you should because it filters for serious customers and gives you budget to actually build good stuff",
    
    "okay meeting agenda brainstorm um so we need to cover the product roadmap for Q2 and we need to talk about the hiring plan because we've got three open roles and also there's the budget review that finance wants us to do but that might be its own meeting actually and then there's the thing with the contractor situation Mike's contract ends next month and we haven't decided whether to extend or hire someone full time and I think we should discuss that with the team and oh also the team social which Sarah keeps reminding me about we need to pick a date before everyone's calendars fill up",
    
    "been reading about stoicism lately and the main ideas are basically that you should focus on what you can control and let go of what you can't which sounds obvious but is actually really hard to apply consistently and the thing that struck me is that Marcus Aurelius was dealing with running an empire wars plagues betrayals and he was still practicing these mental exercises every day writing in his journal reflecting on his reactions and I think the modern equivalent is probably journaling and meditation but also just being more intentional about where you put your attention because attention is the currency right if you're spending it on things you can't control you're basically broke",
    
    "the website redesign so we've been going back and forth on this for months and I think we need to just make a decision so here are the options option one is a full redesign from scratch which gives us maximum flexibility but takes 4-6 months and costs a lot option two is a reskin we keep the structure but update all the visuals and copy which is faster maybe 6-8 weeks but we'd still have the UX problems that users complain about and option three is incremental improvements we fix the top 5 user complaints one by one and it's less risky but never feels done and I personally think option two is the pragmatic choice but the CEO wants option one and the dev team wants option three",
    
    "anxiety thoughts I know this is supposed to be a voice note but I just need to get it out of my head so I'm stressed about the presentation on Friday because the board will be there and I haven't presented to the board before and I know I know my material but I get in my head and then I stumble and then I get more nervous and it becomes a spiral and the last time I presented to a big group was that all-hands in July and it went okay actually it went fine but it felt terrible from inside and I don't know if I should practice more which might make me overthink it or just trust my preparation",
    
    "startup idea what if there's a browser extension that like tracks which news articles you've read and cross-references them against a bias database and shows you a little indicator of like how biased your news diet is in real time and it could suggest articles from different perspectives on the same topic to kind of challenge your bubble and it could also track topics you haven't read about at all maybe important things you're missing and there could be a weekly report like your news diet this week and it could be gamified you earn points for reading stuff from perspectives you don't normally read",
    
    "grocery shopping but I'm also meal planning so okay this week I want to make that Thai curry so I need coconut milk lemongrass fish sauce and the paste and also vegetables so broccoli peppers and um snap peas I think and then for the pasta dish I need pasta obviously tomatoes garlic and some basil and I'll do chicken too so chicken breast and actually I should check what's in the fridge already before I buy too much and breakfast stuff I need eggs and yogurt and granola and fruit and for snacks maybe some nuts and dark chocolate",
    
    "reviewing the quarter so revenue came in at 2.3 million which was 8 percent below target and the main reasons are first we lost two enterprise clients one left for a competitor one reduced their contract significantly second the SMB segment actually grew 12 percent which is encouraging third we had three deals that were supposed to close in Q4 push to Q1 so that's recoverable the team performed well given the circumstances morale is okay but people are worried about the miss and what it means for bonuses next quarter the pipeline looks stronger we have 15 deals in late stages and if 60 percent close we'd be back on track",
    
    "I was talking to a friend last night and she said something that really stuck with me she said the reason most people don't achieve their goals isn't lack of ability it's lack of consistency and consistency comes from systems not motivation and that resonated because I'm definitely a motivation-based person I get really excited about things and then lose steam and what I need is to build the boring habits the daily check-ins the weekly reviews the automatic processes that keep things moving even when I don't feel like it and the counterintuitive thing she said was that the systems have to be so easy to maintain that even on your worst day you'll still do them",
    
    "the podcast episode ideas we discussed so first one is interviewing founders who failed and what they learned second is a deep dive on pricing strategies third could be about remote work culture specifically how to maintain culture without an office fourth could be about hiring your first 10 employees and fifth actually Dave suggested we do a listener question episode where we answer questions sent in by email and that would be good for engagement and community building so that's basically five episodes enough for a month and then we need to plan the production schedule editing cover art show notes and distribution",
    
    "okay thinking about health goals for this year so first fitness I want to run a 5K without stopping which means I need to start training now probably couch to 5K again and also I want to go to the gym twice a week at minimum and not just going but actually following a program not just random machines and then diet wise I want to reduce alcohol to weekends only and eat more vegetables and less processed stuff and sleep I need to fix my sleep the goal is 7 hours minimum and actually get off screens by 10pm and mental health wise I want to continue therapy and maybe start meditating again I did it for 3 months last year and it really helped",
    
    "debugging session notes so the issue is that the batch job fails about 30 percent of the time intermittently and there's no consistent pattern we can find it seems to happen more when the database load is high but we can't confirm that because we don't have proper metrics set up the error is a timeout but the timeout value is set to 5 minutes and these jobs should never take more than 30 seconds so something is blocking and my theory is it's a lock contention issue because we do a lot of concurrent writes but Tom thinks it's a network issue and Sarah hasn't had time to look at it yet",
    
    "what I learned this week so first technical thing I learned about Redis pub/sub and how it's different from queues basically it's fire and forget meaning if no subscriber is listening the message is lost which is a problem for critical notifications second non-technical thing I learned about having hard conversations specifically that you should separate the observation from the judgment so instead of saying you're always late say I noticed you were 15 minutes late to the last three meetings and that approach is much less defensive and more likely to lead to a good conversation third thing I'm still processing is about context switching and how expensive it is cognitively",
    
    "the client call went sideways so they came in wanting a simple website but then started adding requirements throughout the call and by the end they wanted a full e-commerce platform with custom inventory management integrated with their existing ERP which is SAP and the budget they mentioned at the start was 5000 pounds which is completely unrealistic for what they now want I tried to set expectations but they kept saying things like it doesn't need to be complicated and every time I explained the complexity they'd say yes but surely that's not hard and I need to document everything from this call and send a proper scoping document with realistic estimates",
]

AGENTIC_TRANSCRIPTS = [
    # BUILD tasks
    "build me a full-stack web app for tracking daily habits users can add custom habits mark them complete each day and see a streak counter and some analytics use Next.js for frontend and supabase for the backend I want it to look clean and minimal maybe dark mode",
    
    "I need a Python script that monitors a folder for new CSV files processes them validates the data against a schema logs any errors and moves processed files to an archive folder and failed files to an error folder it should run continuously as a daemon",
    
    "create a REST API with FastAPI for a simple todo application with full CRUD endpoints user authentication with JWT tokens PostgreSQL database with SQLAlchemy and Docker setup for easy deployment include proper error handling and API documentation",
    
    "build a Discord bot that um basically monitors a server for messages containing certain keywords sends an alert to a designated channel logs all matches to a database and has slash commands for admins to add or remove keywords from the watchlist",
    
    "I want a Chrome extension that tracks how much time you spend on different websites shows a daily summary popup limits you to a set amount of time per site and syncs across devices using Chrome sync storage",
    
    "create a machine learning pipeline for sentiment analysis on customer reviews loads data from CSV trains a BERT model evaluates performance saves the model and creates a simple Flask API endpoint to serve predictions",
    
    "build a CLI tool in Rust that takes a markdown file and converts it to PDF with custom styling supports code syntax highlighting table of contents generation and has a watch mode that rebuilds on file changes",
    
    "make a React Native mobile app for expense tracking with receipt photo capture OCR to extract amounts and merchants categorization manually or auto and monthly spending reports synced to a backend API",
    
    "create a Terraform configuration to set up a production-ready AWS infrastructure with VPC ECS cluster RDS postgres load balancer auto-scaling and proper IAM roles for a containerized Node.js app",
    
    "build a GitHub Actions workflow that runs tests on every PR lint checks security scanning builds a Docker image pushes to ECR and deploys to staging automatically with a manual approval gate for production",
    
    # REFACTOR/FIX tasks
    "I have a Python flask app with no tests and everything is in one file app.py I need you to refactor it into a proper structure with blueprints database models in separate files a config module unit tests for all business logic and type hints throughout",
    
    "the React component I showed you last time has some performance problems it rerenders way too much I think because we're not using useMemo or useCallback properly and the state management is all over the place can you refactor it to use proper memoization and maybe move some state to context or Zustand",
    
    "we have a SQL query that takes 45 seconds to run on our production database I need you to optimize it here's roughly what it does joins 6 tables groups by user computes aggregates over the last 90 days and the main table has 50 million rows the indexes might need updating too",
    
    "our Node.js microservice is leaking memory it grows from 200MB to 2GB over about 8 hours and then crashes I suspect it's either event listeners not being cleaned up or a caching issue can you help me find the leak systematically",
    
    "the authentication in our app was built 3 years ago and uses MD5 for passwords which is obviously terrible I need to migrate to bcrypt with proper salt rounds without invalidating existing user sessions and without any downtime",
    
    # RESEARCH/COMPARE tasks
    "I need to choose a vector database for our RAG system the scale is about 10 million embeddings with 1536 dimensions we need good semantic search sub-100ms latency managed cloud hosting preferred main options I'm considering are Pinecone Weaviate and Qdrant compare them on performance cost developer experience and managed vs self-hosted options",
    
    "we're choosing between GraphQL and REST for our new API compare them for our use case we have a mobile app with complex data requirements multiple clients frontend and mobile different data needs and a team of 5 engineers who are more familiar with REST include trade-offs on caching complexity tooling and long-term maintenance",
    
    "need to decide on state management for a complex React app with about 50 screens lots of async data user authentication and real-time updates compare Redux Toolkit Zustand and React Query plus Context tell me which you'd recommend for this specific use case and why",
    
    "comparing Kubernetes versus Docker Swarm for deploying our microservices we have 12 services moderate traffic around 10k daily users a small DevOps team of 2 and we want something we can manage without being Kubernetes experts but also something that scales",
    
    "I want to add full-text search to our app options are Elasticsearch Algolia Typesense and just using PostgreSQL full-text search our scale is about 500k documents query volume maybe 1000 per hour and we need good relevance ranking and typo tolerance",
    
    # BRAIN DUMP / HIGH LEVEL IDEAS
    "okay so I have this idea for an app right like what if you could voice record your thoughts throughout the day and then at night the AI like organizes everything into your journal groups similar thoughts together identifies action items and even notices patterns over time like you keep mentioning stress about money or you keep having ideas about that side project and it builds up this picture of your mental life and maybe suggests things like hey you've been stressed about work for 3 weeks maybe think about that",
    
    "thinking about starting a SaaS business in the developer tools space specifically around code review automation idea is that the AI doesn't just find bugs but actually understands the context of your codebase what patterns you use what your team's preferences are and then makes suggestions that are actually relevant not just generic stuff and it learns from which suggestions you accept or reject and gets smarter over time",
    
    "so I want to build something for small restaurants that helps them um basically understand their business better most of them don't have the time to do analytics they just want to know what's selling what's not what time of day is busy when to have more staff and what's making them the most money the current options are either too expensive or too complex so the gap is a simple easy to use thing that connects to their POS system",
    
    "my idea is basically an AI pair programmer that's specifically trained on your company's codebase so it knows all your internal patterns your naming conventions your architectural decisions your existing utilities and helpers so when you ask it to implement something it actually writes code that fits in rather than generic boilerplate",
    
    "I'm thinking about a platform for freelancers that handles the whole business side automatically invoicing from tracked time client communication templates tax estimation compliance reminders and maybe even predictive cash flow so you know when you're going to have a slow month before it happens",
    
    # WITH CORRECTIONS
    "build a task management app actually wait let me be more specific build a project management tool like Jira but way simpler with boards cards actually I want it more like Linear than Jira so tasks issues with priorities labels and a clean kanban view and maybe a sprint planning feature",
    
    "I need a script that um pulls data from the database wait no pulls data from the API not the database from the GitHub API specifically and um generates reports no wait creates a dashboard that shows pull request metrics like time to review time to merge and review coverage",
    
    "create a mobile app no actually start with a web app that helps people um what's the word track their medication and get reminders and the backend should be in Node no Python actually no use whatever is faster let's say Node with TypeScript",
    
    "I want you to build a chatbot that integrates with our website actually make that a widget that pops up uses our FAQ data basically a RAG system to answer customer questions and escalates to human support if it can't answer with confidence threshold maybe 0.7",
    
    "set up monitoring for our production app um specifically I want alerts when error rate exceeds 1 percent no 0.5 percent and latency P99 goes above 2 seconds and CPU over 80 percent for more than 5 minutes use whatever monitoring stack you think is best probably Prometheus and Grafana",
    
    # FILLER-HEAVY TECHNICAL
    "so basically um like I kind of need a way to like you know automatically um like test our API endpoints and like basically generate a report of which ones are um you know like slow or like returning wrong status codes",
    
    "um like so the thing is basically I need to like set up a you know like a caching layer um basically Redis or something like that to like you know speed up our database queries which are kind of like taking too long",
    
    "so um basically I want like a script that um you know monitors our like server and like um basically sends me a like notification on Slack um if like the disk space gets like too low or something",
    
    # MULTI-PHASE ENGINEERING
    "plan and implement a microservices architecture migration for our monolithic Express app we have about 20 distinct features I want to start with extracting the user service and payment service first keep everything else in the monolith for now use Docker and an API gateway and make sure we can deploy each service independently with zero downtime",
    
    "I need a complete data pipeline from our PostgreSQL production database to a data warehouse for analytics use Airbyte for extraction Airflow for orchestration dbt for transformation and Snowflake as the destination give me the setup configuration and a sample dbt model",
    
    "implement end-to-end encryption for our chat application messages should be encrypted client-side using the Signal protocol key exchange on first message key rotation every 100 messages and we need to handle multi-device scenarios where the same user is on mobile and desktop",
    
    "build a recommendation engine for our e-commerce app collaborative filtering for users with enough purchase history and content-based filtering for new users item embeddings using product descriptions and categories with a Redis cache for pre-computed recommendations and an A/B testing framework",
    
    "create a comprehensive observability stack for our Kubernetes cluster distributed tracing with Jaeger metrics with Prometheus and Grafana log aggregation with Loki and Grafana alerts for the main SLOs and a runbook template for common incidents",
    
    # SPECIFIC TECHNICAL REQUIREMENTS
    "implement a rate limiter for our API using a sliding window algorithm Redis backed should handle 1000 requests per minute per API key with burst allowance of 50 requests return proper 429 responses with retry-after headers and log all rate limit events",
    
    "create a database migration strategy to add full-text search to our existing PostgreSQL database without downtime we have 20 million rows the search should support multiple languages fuzzy matching and ranking by relevance and recency",
    
    "build a real-time collaborative document editor like Google Docs for code using operational transforms or CRDTs WebSocket for sync conflict resolution and cursor presence showing other users I know this is complex but focus on the core sync mechanism first",
    
    "implement a multi-tenant SaaS architecture where each tenant has complete data isolation either row-level security in a shared database or separate schemas the system needs to handle up to 10000 tenants each with up to 1 million rows",
    
    "I need OAuth 2.0 implementation with PKCE for our mobile app supporting Google GitHub and Apple sign-in with a custom authorization server not just using Auth0 because we need full control include refresh token rotation and token revocation",
    
    # ARCHITECTURE/DESIGN
    "design the architecture for a high-traffic notification system needs to handle 10 million push notifications per day across iOS Android and web real-time delivery tracking deduplication retry logic and a way for users to manage their notification preferences",
    
    "what's the best way to structure a Next.js app that has both a public website and a private dashboard shared components but different layouts authenticated routes and we want to use server components as much as possible with selective client components",
    
    "design a CQRS and event sourcing architecture for our financial transaction system we need complete audit trail of all state changes ability to replay events to rebuild state and projections for different read models",
    
    "how should we structure our monorepo with a Next.js frontend React Native mobile app shared TypeScript types utility libraries and multiple Node.js backend services I want consistent tooling code sharing without duplication and fast CI builds",
    
    "plan the backend architecture for a real-time multiplayer game with about 1000 concurrent users per room rooms need to sync state at 60fps handle disconnects gracefully anti-cheat at the server level and scaling horizontally",
    
    # EDGE CASES
    "make it work",
    "the thing from last time but better",
    "um just like do the AI stuff for the project",
    "fix the bug you know the one",
    "build the entire app",
    
    # REAL-WORLD TRANSCRIPT (James's)
    JAMES_REAL_TRANSCRIPT,
    
    # MORE SPECIFIC BUILD TASKS
    "create a webhook handler in Express that receives GitHub events validates the signature queues processing jobs using Bull processes push events to trigger deployments and pull request events to run automated code review",
    
    "build a CLI tool for database management that can diff two database schemas show migration paths generate migration scripts and apply them with dry-run support and rollback capability works with PostgreSQL and MySQL",
    
    "implement a smart retry mechanism for our HTTP client with exponential backoff jitter circuit breaker pattern and configurable retry budgets per service so we don't cascade failures across services",
    
    "create a code generation tool that takes a database schema as input and generates TypeScript types API endpoints with full CRUD Drizzle ORM models and React Query hooks for the frontend",
    
    "build a feature flag system that stores flags in Redis allows targeting by user ID percentage rollout and user attributes supports emergency kill switches and has a simple admin UI and SDK for both frontend and backend",
]

# ─────────────────────────────────────────────
# SCORING PROMPT
# ─────────────────────────────────────────────

SCORING_PROMPT = """You are evaluating the quality of a voice-to-text transcript formatter.

Original transcript: {transcript}

Formatted output: {output}

Score this output from 0-100 based on these criteria:
1. **Accuracy** (0-25): Does output capture the intent without adding/losing important info?
2. **Format correctness** (0-25): Is the format appropriate for the content type (list/message/command/notes/prose)?
3. **Filler removal** (0-20): Are filler words (um, uh, like, basically, sort of, you know) removed?
4. **Self-correction handling** (0-15): Are corrections handled correctly (final version used, not intermediate)?
5. **No hallucinations** (0-15): No invented content not in the original?

Special rules:
- If transcript is empty/noise/nonsense and output is empty string → score 100
- If transcript is empty/noise but output has content → score 0
- If output contains meta-commentary like "Here is the formatted..." → penalize -15
- If output contains filler words from transcript → penalize proportionally

Return ONLY a JSON object like this:
{{"score": 85, "breakdown": {{"accuracy": 22, "format": 23, "filler_removal": 18, "self_correction": 13, "no_hallucination": 14}}, "issues": ["brief issue description if any"], "good_points": ["what worked well"]}}"""

def load_prompt(filename):
    path = os.path.join(PROJECT_DIR, "prompts", filename)
    with open(path, 'r') as f:
        return f.read()

def save_prompt(filename, content):
    path = os.path.join(PROJECT_DIR, "prompts", filename)
    with open(path, 'w') as f:
        f.write(content)
    print(f"  ✅ Saved: {path}")

def run_prompt(prompt_template, transcript):
    """Run a transcript through GPT-4o-mini with the given prompt."""
    prompt = prompt_template.replace("{transcript}", transcript)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  API error: {e}")
        time.sleep(2)
        return ""

def score_output(transcript, output):
    """Score a transcript+output pair using GPT-4o-mini."""
    prompt = SCORING_PROMPT.format(transcript=transcript, output=output)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=500,
        )
        text = response.choices[0].message.content.strip()
        # Extract JSON
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"score": 0, "breakdown": {}, "issues": ["Failed to parse score"], "good_points": []}
    except Exception as e:
        print(f"    ⚠️  Score error: {e}")
        return {"score": 50, "breakdown": {}, "issues": [str(e)], "good_points": []}

def analyze_failures(failures, mode_name):
    """Use GPT-4o-mini to analyze common failure patterns and suggest prompt improvements."""
    examples = []
    for f in failures[:15]:
        examples.append(f"TRANSCRIPT: {f['transcript'][:200]}\nOUTPUT: {f['output'][:200]}\nISSUES: {', '.join(f['score_data'].get('issues', []))}\nSCORE: {f['score']}")
    
    analysis_prompt = f"""You are a prompt engineer analyzing failures in a voice-to-text formatter called "{mode_name}".

Here are the {len(failures)} worst-performing cases (lowest scores):

{chr(10).join(f'---CASE {i+1}---{chr(10)}{ex}' for i, ex in enumerate(examples))}

Analyze the patterns:
1. What types of transcripts are failing?
2. What specific formatting/handling mistakes are recurring?
3. What rules are missing from the current prompt?

Return a JSON with:
{{
  "failure_patterns": ["pattern1", "pattern2", ...],
  "missing_rules": ["rule1", "rule2", ...],
  "recommendations": ["specific fix 1", "specific fix 2", ...]
}}"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": analysis_prompt}],
            temperature=0.1,
            max_tokens=800,
        )
        text = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        print(f"    ⚠️  Analysis error: {e}")
    return {"failure_patterns": [], "missing_rules": [], "recommendations": []}

def improve_prompt(original_prompt, analysis, mode_name, failures):
    """Generate an improved prompt based on failure analysis."""
    examples_for_improvement = []
    for f in failures[:10]:
        examples_for_improvement.append(
            f"Bad example - Input: '{f['transcript'][:150]}' → Bad output: '{f['output'][:150]}'"
        )
    
    improvement_prompt = f"""You are improving a voice-to-text formatting prompt for "{mode_name}".

CURRENT PROMPT:
{original_prompt}

FAILURE ANALYSIS:
Patterns: {json.dumps(analysis.get('failure_patterns', []))}
Missing rules: {json.dumps(analysis.get('missing_rules', []))}
Recommendations: {json.dumps(analysis.get('recommendations', []))}

BAD EXAMPLES TO FIX:
{chr(10).join(examples_for_improvement)}

Write an improved version of the prompt that addresses these failures.
Key improvements needed:
- Better handling of the failure patterns
- Add missing rules  
- Maintain all the original prompt's strengths

IMPORTANT: Return ONLY the improved prompt text. No preamble, no explanation. Just the prompt."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": improvement_prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    ⚠️  Improvement error: {e}")
        return original_prompt

def run_mode_test(mode_name, prompt_filename, transcripts, rate_limit_delay=0.3):
    """Run full test for one mode. Returns results dict."""
    print(f"\n{'='*60}")
    print(f"🧪 TESTING MODE: {mode_name}")
    print(f"{'='*60}")
    
    prompt = load_prompt(prompt_filename)
    results = []
    
    print(f"  📝 Running {len(transcripts)} transcripts...")
    for i, transcript in enumerate(transcripts):
        if i % 10 == 0:
            print(f"    Progress: {i}/{len(transcripts)}")
        
        output = run_prompt(prompt, transcript)
        time.sleep(rate_limit_delay)
        
        score_data = score_output(transcript, output)
        time.sleep(rate_limit_delay)
        
        score = score_data.get("score", 0)
        results.append({
            "index": i,
            "transcript": transcript,
            "output": output,
            "score": score,
            "score_data": score_data,
        })
    
    avg_score = sum(r["score"] for r in results) / len(results)
    print(f"  📊 Average score (v2 prompt): {avg_score:.1f}")
    
    # Sort by score, get worst 20
    sorted_results = sorted(results, key=lambda x: x["score"])
    worst_20 = sorted_results[:20]
    
    print(f"  🔍 Worst 20 scores: {[r['score'] for r in worst_20]}")
    
    # Analyze failures
    print(f"  🔬 Analyzing failure patterns...")
    analysis = analyze_failures(worst_20, mode_name)
    print(f"  📋 Patterns found: {len(analysis.get('failure_patterns', []))}")
    
    # Generate improved prompt
    print(f"  🛠️  Generating improved prompt...")
    improved_prompt = improve_prompt(prompt, analysis, mode_name, worst_20)
    
    # Test improved prompt on worst 20
    print(f"  🔁 Testing improved prompt on worst 20...")
    improved_results = []
    for r in worst_20:
        new_output = run_prompt(improved_prompt, r["transcript"])
        time.sleep(rate_limit_delay)
        new_score_data = score_output(r["transcript"], new_output)
        time.sleep(rate_limit_delay)
        new_score = new_score_data.get("score", 0)
        improved_results.append({
            "transcript": r["transcript"],
            "old_output": r["output"],
            "new_output": new_output,
            "old_score": r["score"],
            "new_score": new_score,
            "score_data": new_score_data,
        })
    
    old_avg_worst = sum(r["old_score"] for r in improved_results) / len(improved_results)
    new_avg_worst = sum(r["new_score"] for r in improved_results) / len(improved_results)
    improvement = new_avg_worst - old_avg_worst
    
    print(f"  📈 Worst-20 before: {old_avg_worst:.1f} → after: {new_avg_worst:.1f} (Δ{improvement:+.1f})")
    
    # Save improved prompt if it improves by >1pt
    v3_filename = prompt_filename.replace(".txt", "_v3.txt")
    saved_v3 = False
    if improvement > 1.0:
        print(f"  ✅ Improvement > 1pt! Saving as {v3_filename} and replacing original...")
        save_prompt(v3_filename, improved_prompt)
        save_prompt(prompt_filename, improved_prompt)
        saved_v3 = True
    else:
        print(f"  ℹ️  Improvement ≤ 1pt ({improvement:+.1f}), not replacing original")
        # Still save v3 for reference
        save_prompt(v3_filename, improved_prompt)
    
    # Pick top 3 best/worst examples for report
    best_3 = sorted(results, key=lambda x: -x["score"])[:3]
    worst_3 = sorted(results, key=lambda x: x["score"])[:3]
    
    # Before/after examples (top 3 improvers)
    before_after = sorted(improved_results, key=lambda x: x["new_score"] - x["old_score"], reverse=True)[:3]
    
    return {
        "mode": mode_name,
        "prompt_file": prompt_filename,
        "total_transcripts": len(transcripts),
        "avg_score": avg_score,
        "min_score": min(r["score"] for r in results),
        "max_score": max(r["score"] for r in results),
        "score_distribution": {
            "90-100": sum(1 for r in results if r["score"] >= 90),
            "80-89": sum(1 for r in results if 80 <= r["score"] < 90),
            "70-79": sum(1 for r in results if 70 <= r["score"] < 80),
            "60-69": sum(1 for r in results if 60 <= r["score"] < 70),
            "below_60": sum(1 for r in results if r["score"] < 60),
        },
        "failure_analysis": analysis,
        "improved_prompt_improvement": improvement,
        "saved_v3": saved_v3,
        "avg_score_worst20_before": old_avg_worst,
        "avg_score_worst20_after": new_avg_worst,
        "worst_20": worst_20,
        "best_examples": [{"transcript": r["transcript"][:100], "output": r["output"][:200], "score": r["score"]} for r in best_3],
        "worst_examples": [{"transcript": r["transcript"][:100], "output": r["output"][:200], "score": r["score"]} for r in worst_3],
        "before_after_examples": before_after[:3],
        "all_results": results,
    }

def test_james_transcript(modes):
    """Test James's real transcript through all 3 modes."""
    print(f"\n{'='*60}")
    print(f"🎤 TESTING JAMES'S REAL TRANSCRIPT")
    print(f"{'='*60}")
    print(f"  Transcript: {JAMES_REAL_TRANSCRIPT}")
    
    james_results = {}
    for mode_name, prompt_filename in modes:
        prompt = load_prompt(prompt_filename)
        output = run_prompt(prompt, JAMES_REAL_TRANSCRIPT)
        time.sleep(0.5)
        score_data = score_output(JAMES_REAL_TRANSCRIPT, output)
        time.sleep(0.5)
        james_results[mode_name] = {
            "transcript": JAMES_REAL_TRANSCRIPT,
            "output": output,
            "score": score_data.get("score", 0),
            "score_data": score_data,
        }
        print(f"\n  [{mode_name}] Score: {score_data.get('score', 0)}")
        print(f"  Output: {output[:300]}...")
    
    return james_results

def main():
    print("🚀 Waffler v10 — Deep Transcript Testing")
    print(f"   Testing all 3 modes with 100 transcripts each")
    print(f"   Results → {RESULTS_FILE}")
    
    # Pad/trim transcript lists to exactly 100
    smart_list = (SMART_TRANSCRIPTS * 2)[:100]
    ramble_list = (RAMBLE_TRANSCRIPTS * 6)[:100]  # ramble has 20, need 100
    agentic_list = (AGENTIC_TRANSCRIPTS * 2)[:100]
    
    # Verify counts
    print(f"\n  📊 Transcript counts: Smart={len(smart_list)}, Ramble={len(ramble_list)}, Agentic={len(agentic_list)}")
    
    modes = [
        ("Normal (Smart)", "smart.txt"),
        ("Ramble", "adhd_ramble.txt"),
        ("Agentic Engineer", "agentic_engineering.txt"),
    ]
    
    all_results = {}
    
    # Test James's transcript first (using original prompts)
    james_results = test_james_transcript(modes)
    all_results["james_real_transcript"] = james_results
    
    # Run all 3 modes
    transcript_sets = {
        "Normal (Smart)": smart_list,
        "Ramble": ramble_list,
        "Agentic Engineer": agentic_list,
    }
    
    mode_results = {}
    for mode_name, prompt_filename in modes:
        transcripts = transcript_sets[mode_name]
        result = run_mode_test(mode_name, prompt_filename, transcripts)
        mode_results[mode_name] = result
    
    all_results["modes"] = mode_results
    
    # Summary
    print(f"\n{'='*60}")
    print("📋 FINAL SUMMARY")
    print(f"{'='*60}")
    for mode_name in mode_results:
        r = mode_results[mode_name]
        print(f"\n  {mode_name}:")
        print(f"    Average score: {r['avg_score']:.1f}/100")
        print(f"    Range: {r['min_score']} - {r['max_score']}")
        print(f"    Score distribution: {r['score_distribution']}")
        print(f"    Improvement on worst 20: {r['improved_prompt_improvement']:+.1f}pt")
        print(f"    v3 prompt saved: {r['saved_v3']}")
    
    print(f"\n  James's real transcript scores:")
    for mode, jr in james_results.items():
        print(f"    {mode}: {jr['score']}/100")
    
    # Save results (strip very large data for json)
    for mode_name in mode_results:
        r = mode_results[mode_name]
        # Keep all_results but truncate outputs for filesize
        for item in r.get("all_results", []):
            item["transcript"] = item["transcript"][:300]
            item["output"] = item["output"][:500]
        for item in r.get("worst_20", []):
            item["transcript"] = item["transcript"][:300]
            item["output"] = item["output"][:500]
    
    with open(RESULTS_FILE, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    print(f"\n✅ Results saved to {RESULTS_FILE}")

if __name__ == "__main__":
    main()
