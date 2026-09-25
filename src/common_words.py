"""Everyday English words, for telling a misheard name from ordinary speech.

Custom vocabulary correction (``transcribe_whisper.fuzzy_match_word``) joins
two adjacent words and compares the join with each vocabulary entry, because
Whisper sometimes splits an unfamiliar name into two words ("Ashkan" heard as
"Nash can"). But two ordinary words side by side can also land close to a
name: "add an" and "said and" read as Aidan, "is hotel" as Isobel, "colour
card" as Clubcard. When both words are in this list, the pair is taken as
what the speaker said unless the join spells the entry, or differs from it
only in its vowels (see ``_bigram_join_matches``).

Contents: the 4,000 most frequent words of the ranking below, then a short
hand-checked supplement of British and conversational words (mum, telly,
colour, favourite, cheers, ...). The ranking is the order of whole-word
tokens in the Whisper / GPT-2 tokenizer vocabulary (MIT licence), which
follows corpus frequency, filtered to real words with the Hunspell en_GB and
en_US dictionaries. A word of four letters or fewer must be a headword in
both dictionaries (or the plural of one), or be in the EFF diceware lists,
the BIP-39 English list, scikit-learn's English stop words or the
supplement; that drops subword fragments such as "des" and "sch". A few
dictionary abbreviations remain ("pro", "def"); they are harmless here. No
personal data was used.

Deliberately NOT here: names, and rarer words such as "ash" or "nash", so a
split name like "Nash can" or "Ash can" can still be joined.
"""

COMMON_WORDS = frozenset("""
a the to you and in of that is it be we on for this go do have an with are so
as me was can not like at he con or your they what all just pro my there but
know about one get out will from up if don us here some going think our want
really see by more then thing them right people when time who because no look
has would how say now man very work which any act also these did had every way
their need part were back make got cont other good been over per lot where
first his imp little much comp dis let her things start app could inter those
well two kind i differ something take she talk even down use off actually does
new bet day only him again gonna comm through come than may call different
should put under inc feel said try help show bit play quest many pers great
most doing being too rem sec add same mean find end still min years around
after point long before person big real next love video fin today par stud
import give die tell why sure set life world always hand cons fun important
made ask car last three happen inst def might year own keep another mod guys
able its place rep exper high done better reg care never met exam used trans
second best hard problem watch course question col interest cor each system
fact run top thought everything count together game already called understand
mom using number looking pretty getting week side form bus ass turn cur coll
dire open such ear friend ext home water move char both between thank maybe
away name music change pass build few list old sort gener yeah while anything
power coming word belie found means inform small came working example head
whole trying case probably okay plan sit school process far read sol support
started cap left data times wanted talking having sub term went child den
light book four hope making opp money program stand sign learn team rest lead
nice hum comes cent mark level develop test stuff class war everyone check
sing art follow pub looks enough bra without idea ever once bring saying hear
less mind full bad someone won health information stop group quite remember
family business spec seen month says live line moment sound cool certain must
kid seem else questions story pop order makes since though product leg meet
super pay para job believe wait quick men days goes speak miss design project
cut mill particular pie students five free prof connect experience stay food
hist please community body hour reason later house state past walk hold organ
effect yourself plus room become night break pain step nothing until during
begin pot mar clear face grow inside friends leave easy area eat saw answer
front beaut matter son result fut focus interesting create press pick took ref
exact space told public view couple heart ready almost half least basically
yet taking country win possible cam pat wanna consider abs within human
thinking oh kill invest whether control against present watching govern works
air tot vol close bro film type happened girl pour color sense norm future
deal whatever young guess perform hit direct often along click situ happy ago
enc myself cover mid cost ten expect strong share true minutes mess wrong yes
allow subs fore fight social ways short fall law enjoy access non across link
terms land special forward research main record definitely either listen key
market videos guy fig mention song intern value bar single gets sometimes
perfect red post elect current heard simple everybody needs black given stat
children prom pull company beautiful amazing success discuss difficult camp
buy mag behind women channel report sent event energy words ahead tool exactly
favor low proper relations kids entire ones city six happens model especially
soon avail orig asked continue others lay based prop hot fast protect amount
fund custom cult hands haven outside hat treat mater building educ sum finish
large white dam box opt playing available port early wonder function morning
cir deep million pan government write wind trad relationship neg text fine
months hours pod collect history below ant comb exist takes character field
piece reach material dig phys similar net position learning clean cook seems
touch major itself screen known environ final figure eyes seeing hair news
completely knew situation phone ball parent sorry instead huge individual fire
running somebody anal offer feeling third series draw service cannot living
difference opportunity near local object quickly issues prep profess foot
usually general members equal moving specific fur common attack account grand
self content gives normal impact dies lab finally doll taken ground gave lost
worked liter issue return happening wants problems size obviously mass felt
bottom grad bur gone soft points camera sever middle games cell sleep deg
drink environment talked choose rather mentioned fill track rad pack send
engine door longer language extra webs beginning refer forget comment dark
anyone prim mix starting provide mother period stick technology bed giving
explain represent lives tom excited card teach welcome wall chance simply
several created push higher online ton favorite looked button bill changes
slow death themselves cop personal needed study disc address match straight
dog deb correct including jump media subscribe page towards tried paper
picture version brought percent god decided select further comments dream
center woman road fail became manage action speed shoot apply cause father
code role green built flow base training round path interested respect changed
student approach shows tar dead thous absolutely mic practice quality lower
review ok concern fair website travel risk board parents feed save serious van
worth search parts movie method ill wish item minus voice skin areas eight oil
baby employ places fix lots season table attention age network doc total price
crazy sat thanks stage boy accept enter honest development knows image weeks
sex hundred sounds learned bud drop safe fuck cross setting involved worry sun
certainly natural cry education alone eye rate fit pressure services cases
drive fol sin blue affect shot positive brand trust weight asking tax putting
roll original products contact goal pow performance blood temper office
comfort suggest plat seven concept bag feels gotta fan challenge companies pen
meeting visit supposed viol notice minute tit block due force types late
improve ave awesome machine following measure ability tout ideas increase jest
truth spend science episode fish throw tour direction countries industry feet
leaders staff store decision actual section results star mist dad numbers cold
upon log complete brain easier turned writing telling resources party
knowledge anymore fly sell easily range option meaning site disco lose install
message note books becomes himself aware require systems among weird platform
helps luck web negative tut above stories load background switch various kinds
nature pray rev likely understanding brother plant throughout culture
wonderful sold starts written arm rock wear square literally played heat
popular catch mount surface pieces style reading conversation bank egg target
din cos author och email spirit sitting strength bigger mat police waiting
dollars imagine pet hop computer gold security solution patter standard double
wife directly bunch regard sweet unique train description cat college
application multiple president added rob hosp tools gun basic lines structure
totally biggest park regular mine previous policy political shape onto arch
join frame choice tend tor events claim groups eating recently taste skills
shop seconds town options movement birth hang rid spread host subject pal
carry agree career sudden file except potential complex physical date powerful
member spot source fem dry lock zero rout folks launch anyway audience slight
allows fat national interview cute secret prior smart funny related somewhere
creating society gel transform include particularly showing reality drug
feature reasons wrote band earth features floor speaking tip stock church
response slide meant kinda scene players broad tomorrow sea remind document
neigh aspect ourselves hey condition values cast growing user respond appear
progress patients gas batter beat paint sad tree born capital deliver fear
bought stress era helped assist player immediately moved production summer tun
programs average glass trick began till temperature graph rot mob device kept
stream degree helping smell perhaps realize danger loved purpose finished
peace global characters damage allowed medic smaller levels currently modern
contract states adjust scale release prefer mode shall beyond successful
discover therefore cup population spent useful tab quant ice kick steps
tonight grab implement mission clearly appreciate fresh exec projects
communities region however partners minim families evidence pun loss map
anybody changing rules organization essentially element print conditions dance
walking additional fully fans addition liked bow master safety react girls
faith turns tight mouth hospital micro decide patient corner lie bell private
distance warm digital race proud teaching wood colors customers connected
layer achieve perspective cloud ended management rich serve resist thoughts
growth rights charge consist hurt shit beg received meat famous comfortable
medical enjoyed healthy effort doctor military battle fed afraid length
interact according incredible killed daughter schools chicken faster extremely
oppos nous financial exciting journey display memory heavy passed psych
specifically engage led cream edge bull hopefully hate internet budget
property showed thousand poor software necessarily eventually bright demand
threat sir released required vote developed slightly court items challenges
designed hearing listening wearing chem balance receive significant jobs
official perm opportunities overall nation opinion delicious handle necessary
multi campus topic rain panel paid economic street driving hay professional
input fold king wild prevent wide ring title standing although hi sauce sides
animals considered sister shown sac century older truly window location hell
trade critical named prepared tough trip sand apart vie effective limit nine
willing origin elements uses helpful flat familiar core closer active relative
mental random partner rule coffee connection unit anywhere separate testing
sick advantage transfer finding fort economy lack leaving dim discussion chain
users dish careful teacher flu reflect treatment equip planning solve avoid
greater attract photo planet copy visual international laughing thick holding
bringing letter burn effects adult sugar ride highlight nobody chat attach
legal rice rap solid generally pattern transl express examples chose tells tap
experiment benefit expensive generation adding campaign soul maxim salt
calling basis keeping lived occur recent cars traditional held bin expected
focused etc yesterday activity advice opening instance vision tick strategy
electric daily husband station analysis attempt billion forth behavior bless
gut dress experiences fighting mostly pictures mad models trouble bird produce
married theory leader images expand knowing drag brush names sched destroy
forgot raise contain gift request shut degrees benefits studies ends
everywhere hero materials worse decisions foreign realized weak scared covered
individuals compared cert brief activities fab classes missing introduce
equation painting encourage ship exercise thousands row pandemic skill
director milk nut motion closed credit cheese glad highly reduce depending
sharing caught passion farm cells sky pin gather plug university fantastic
hole broken alive tank cart bound customer reaction session plans download cab
instr teams goals transport animal costs calls dial weather shared smooth
leaves shift butter recognize volume detect lift clothes arr stuck fucking
joining included yellow limited notes phase yang sett magic ensure spring
shock wheel cancer root output commit workers civil brings becoming command
update opened statement screw cards task evening stitch ban freedom normally
provided bike noise climate increasing personally legs depend variety wash
quiet forever counter slowly noticed fell whenever versus plane suit boss
income shad artist plays virus hung constant script snow devices metal sales
collection via gotten wave leading central songs belong photos relax dangerous
suddenly laugh angle worried pap battery lights arms tea former applications
feedback prime expert alright supply leadership typically trees worst busy
presentation strange thin vehicle tired crisis tiny ran forces identify assess
creative department initial infect pump rare dot flex truck plastic likes
rough commer cake actions otherwise rub champ concerned plate equipment taught
motor guide stopped rat labor aim prepare shooting enemy depends lands factor
carbon bread volt waste keeps honor unless plants includes finger stretch
symbol neighbor massive monitor raised businesses earn mobile conduct federal
none teachers trend album transition capacity breath nor gain spin anti
somehow laws moments moves predict fundament pure wow island investment bath
harder tips electron bond chair twice mask honestly pens surprised
communication whose stars properly grew divers expression justice pair seat
links rend funding yap mistake dinner organizations birthday bear afford
relationships tag prison species firm score complicated rank opposite picked
neck technical miles primary laughs competition rent chocolate nearly speech
remain crack promise paying adapt movies wire terrible perfectly impossible
turning responsible humans settings joy dealing surround followed possibly
pros candid assign violence rise audio ultimately majority guard brown nervous
theme drawing obvious blow hook circle architect protection watched answers
diet powder yours highest boys lunch sets mole winter lucky responsibility
signal wondering cooking surprise loop jag curious marketing virtual buying
restaurant doubt grant cash faculty wed accident medium crow library reference
fourth filled developing provides poll traffic forms client gentle muss
spending construction universe dude indeed managed applied fairly constantly
eggs radio hide club efforts cities reached harm cutting iron afternoon hall
gosh influence increased vert menu selling quote continues kitchen icon
providing technique component bye solutions assume liquid quarter female
status coach rein combination objects district makeup murder bowl published
sports identity seemed acting upload hast boat cycle loud conflict upper
manager filter recording associated fuel election employees saved wet stupid
lad fest wake greatest seriously feelings beauty conscious lets shoes butt
divided zone maximum runs components arrived confident height talks confidence
leads nose independent minor fashion sexual bun soil empty journal weekend
error forest missed evil storage singing knock impress existing experienced
loves victim tall lens sustain argument factors automatically fruit liber ale
hundreds recipe describe driver meal latest compare string millions correspond
fixed views river studio flavor presence units saving hers functions north
horse pleasure tail explore commercial rates housing situations appropriate
emotional blind joined located dear adopt angry pad attend sample
infrastructure lesson broke maintain artists equals operation crowd internal
tests sou chart vent quand mountain discovered solo bare ingredients scary
spiritual define blog sector clients cultural tradition judge determine
matters pool variable vaccine caused west continued unfortunately flight wrap
huh pink remains losing requires foundation profession younger appears
engineering advent tent reported dent tutorial placed prove cheap esp files
whom excellent belief jack swim essential reports definition produced award
male moon flying sources plenty secure efficient repeat methods calm discussed
server ideal hoping depth heaven habit flag facing gem fellow spoke citizens
pages religious chapter consult marriage medicine dogs instrument wealth grade
crime thread wine insurance properties article underneath joint relatively
inch despite classic supporting instruct administration dollar stronger fabric
crew lady potentially army introduced aspects deploy weapons register sport
bridge inner minimum witness village owner scream pitch advance suppose
differences interpret trial thinks chemical tape conversations distribution
flash understood resistance begins advanced relevant politics football fingers
clip aside toward bat valid completed podcast package pulled south importance
pushing stands homes concerns biz gear odd replace giant gap classroom bug
everyday garden falling fault chest potato buildings operating pare weapon
mixed split emergency profit typical announce committee diam lovely carefully
youth dreams bodies techniques mechanism desire practices sorts herself guitar
sites beach dram plot established inspired announced tech processes rapid
religion smile schedule default forced stone tie drinking served conference
fake diff challenging intelligence studying appoint tan curve failed novel
differently escape described convert hotel walked neighborhood hidden defined
label joke defense entertain policies alcohol gal orange spaces storm resource
bard construct flour restrict entirely breaking twenty causes kiss operations
scientists grown owners courses usual inn nuest category records requirements
deeper apps colleagues offers column exchange sending hello succeed suffering
advert failure drugs academic reward committed standards intention films teeth
struggle diss enemies ocean bottle institution arrest letters creates clock
debt ancient border believed critic bomb ham sight survive jam alles trigger
format decades signs robot soup communicate frequency messages gender prices
false fields mac layers decor walls flip surgery chop agency wanting solar
staying grateful remark technologies protein shoulder sty route debate
possibility invent profile senior apparently precis align knife fool invite
capture dough bite lecture originally choices verse lit measures killing
mirror creation atmosphere tables scenes tone affected mistakes shout portion
previously heads grass offered vector hungry seek doors houses considering
graduate extreme flowers fundamental texture celebrate pill hip supported
agreement purchase recover holes dropped pig ending attacks terror edit
scientific punch puts matrix institutions speaker meters serving database damn
poder sentence signed proof nuclear favour closely index capable sheet sees
naturally participate exists sharp breakfast twist selected presented linear
cable winning absolute engaged dash tested blank reject rear crash colon acid
kit valuable parties colour steel funds lip authority ell recorded delta
reform allowing patterns letting sleeping pizza talent environmental professor
shots contains sequence pounds external happiness establish rig honey symptoms
brows shirt upset guest unlike somewhat hanging rum photograph stable voltage
ghost combat forma march vast commitment dedicated topics machines mini
markets goodness framework basket extent consistent auto mere pulling cow
meetings bast focusing roof positions passing smoke museum intent craft
brothers officer scratch generate emotions ate native blocks faces inches
streets probability pip lying memories practical viewers innovation disappoint
winner ratio documents formula appeared spray wedding gall pepper alternative
gate concert juice dynamic bang scholars crying accurate checking attached
experts shadow delay orient historical planned encounter regarding recovery
defend remote arts chosen flower cameras criminal stack dust hem increases
dying moist languages foods setup effectively wherever hits principle tastes
treated resolution powers bother muscle sale decent coup coast rod bathroom
shopping formed brill mainly contrast shell aller entry launched gig
engagement offering actor returned silver annoy deeply rail prayer hug doctors
ears falls thus therm diversity soy television edges lessons explained temps
surprising hardware thumbs interests developers hitting opposed hearts
represents zoom adults ordered picking filming seed calculate remaining arrive
healthcare awareness squared soldiers revolution trained dancing installed
veter nun importantly emotion teen severe cleaning figured mud raw destroyed
aggress correctly stir extract vehicles parallel lag dare beings excuse alpha
asks pocket appearance insert tempo facility visible tension delivery survey
proceed impressive glue fits boxes controls scenario processing birds confused
accomplish bone strategies personality accepted corn circuit gay manner
confirm entered abandon crop oven suspect carried variables initially depart
fluid clinical anxiety circumstances existence tong spicy facts tons lies
pushed uncle principles rib paste warning musical agreed garlic oxygen stayed
invited embarrass holds dive boost officers seeds buff updates friendly
council piano reduced commission gym interface structures identified channels
bits locations meter beef horrible factory fifth cooked mood velocity
connections domain applying childhood algorithm interaction kidding tomato
continuing nap thumb rush strike evolution windows excess episodes struggling
keys vegetables sup introduction deck loving supports incident boom combined
dirty collaboration priority gran stood locked suppl crochet rooms cabin
accounts facilities linked programming drama instructions waves aid arrange
tack vertical heel narrow knee posted rolling claims intense selection legend
uniform studied agent jog aircraft segment orders platforms myth sensitive
architecture burning behalf therapy involve exposed toil sink agencies stores
accessible speakers diverse enable trading muscles phrase determined riding
sempre driven footage fond jug breathing lighting apartment chief networks
nicely verb revealed tat animation roles versions tasks loose alert figures
creep investigation residents minds applaud coverage expecting operate purple
comparison landscape neither stomach percentage occasion gust urban assistance
surf petit assessment manual improved pilot argue clicking logo outcome gaming
ultimate consumer traveling principal sake mouse relation moral theta golden
whereas healing marry estate suffer physically assets promote duty ai annual
slides carrying fro admit officials voices balls guests roughly victory branch
recognized spell touched agenda parameters instant editor unknown punish
expectations crypt divide hyper agora abuse causing concentration breaks
concrete formal beta heading adventure opens transformation household courage
parking laid tries recall buttons reaching spark canal tube stamp trail
mixture routine county enjoying purposes breast writer reverse distract
deserve logic tact holiday bump worship suck applause patch ladies broadcast
narrative sang movements partnership organized node industrial boring cuts
recon impression editing follows particles skip objective desk primarily
reporting checked smells actors receiving taxes grace competitive division
esper wheels tremendous listed guidance cub intellect regardless conclusion
manifest shame outcomes mail preparing consciousness pants melt module monster
electronic centre stops lamb consequences straw imper extend answered drawn
lips mayor encore blame shower container bass dimension executive delivered
gang pit illness guns airport attitude renew controlled vend nail goods
fishing temple keyboard educational breathe compos sensor performing drum pork
coal ram phones tracks organic dialogue payment array intended bars reviews
kingdom stages mountains dun banks throwing anger humanity shoulders fancy
brilliant inspiration lid shield tang barely margin legit shake convention
contest axis benefic density researchers dice submit dumb extension crush
covering performed protocol hack buddy fascinating equivalent arc proposed
counsel electricity difficulty quit girlfriend cancel frequently closing
decade represented concepts literature bags limits stake voting exposure fried
combine pregnant improvement trim approximately ongoing frank dip tears pause
overcome license repeated categories noodles flood nuts chances bones
efficiency moder omega producing participants jail briefly heal democracy loan
chef uncomfortable repair covers afterwards knees ordinary ticket vary
controller minister tower scar sword papers extraordinary reasonable
contribute pleased updated significantly bot generations protected hiding
neutral tongue pays apple theater strongly opponent holy filling blend
interior bench marks wins distinct east physics peak neat synth reveal roots
acts philosophy matches spots regions diagram rely tens dating coat
acknowledge favourite font finds compass notion inject wisdom forgive finance
demo climb export successfully retire laptop employee contra procedure grid
belt developer spoken aggressive promised equity replaced pose quad speaks
depression retail tort computers searching tub glasses dishes guarantee folder
timing approved wise resident generated stays explanation poison insane
referred outer discussing convenient shapes gray adds capabilities sections
tune tuned leaf choosing regret bonus torn risks characteristics rose
corporate centers nit strict writ extended relief onion babies integrated mens
strategic pointed pride samples domestic webinar suggested heavily instruction
microphone vulnerable continuous poverty blade relate regional tow champion
investing integration pencil rocks rating refuge assistant meaningful
permanent hill wound lake reduction sour steal antes connecting genre complain
hurry monitoring desert nations needle asleep achieved exit scan hurts
cheering occurred seats empower console sessions ridiculous collected
discussions subscribers directions manufacturing criteria mold entering
charged cats plain rings toilet experiencing slip sandwich spectrum hire
nowhere wing rural screens borrow swing touching exception nearby browser
drunk ignore landing draft bedroom deals posit visited cant raising permission
bend champions awful jumping sustainable candy satisfied pipe cock momentum
returns candidates yield maintenance expansion darkness outfit engaging
insight pix quantity ink governments protest articles unable erst annoying
ecosystem maps trash enormous stated brands transportation legislation
providers magazine passes alter farmers confirmed roads worker designs
practicing chill lemon flexible territory lux lifetime gross scroll publish
edition charging whoever fought drill secondary yarn width engineer wings
trauma chip passionate awkward implementation gameplay mystery privacy dump
ships drops observe faced victims gifts boot universal boyfriend cetera
crucial coin shade surely max improving enforcement dirt upgrade restaurants
trap teas proposal passage surrounding upcoming horror clothing decrease
investors sacrifice troops password candidate tasty lighter fallen initiative
notification mush sufficient singer integral transmission abilities chips
approaches sisters drivers rubber sizes tracking impacts arrow largely lawyer
involves delight immediate bullet unlock discount rising pace shorter tener
managing oft vocal lean amounts gentlemen seeking union regularly artificial
impressed tooth yard owned dates configuration requirement belly painful
demonstrate visiting amend vital mate legacy lifestyle formation trailer cave
thrown tricks informed skull functional safely drives tire corresponding
bacteria infection basketball supplies expertise silly elected fired
universities jacket defeat equations thirty streaming designer painted rhythm
affects potatoes opinions stability captured bucks masks compete forgotten
graphics hub crown occurs dual settle pretend violent besides sweat consumers
grocer stun semester ours grams exercises swear illegal harvest threw scope
glory assuming bold constitution frankly employment steam professionals
engineers bitch vice accent mixing enhance swimming immune fare asset radical
separated grip beans pound entrance atom personas dramatic reminds bran blast
greet am ah aw eh ha hm uh um yo ya bay bid cod dug eve fee fog fox gum hut
jar jaw jet lap mug mum nag nil nod oak owe owl paw rag ray rim rip rug sew
shy sip ski sly sob spy sue tin toe toy vet wax wit zip mam mates telly quid
loo cheers fortnight colours honour neighbour realise organise apologise metre
theatre cheque tyre kerb pavement lorry queue whilst amongst reckon sorted
knackered gutted chuffed yep nope emails wifi chatbot emoji texts
""".split())
