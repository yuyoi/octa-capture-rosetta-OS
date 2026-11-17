# Singer patches: where the chains come from

The SINGERS tab in Rosetta Octa Control (`app/strip_patches.py`, group `SINGER`). Each patch takes what is **on record** about a singer's vocal chain, and is built for the Sennheiser e935 into the OCTA-CAPTURE strip:
- **The strip:** HPF, low shelf, one peak, high shelf, tube, comp, gate.
- **ROSETTA FX:** echo + reverb, one input per box. Needs test/strip10.

**What was never published.** No engineer ever gave EQ frequencies for any of these records. Most also never gave compressor settings. What is documented:
- mics
- compressor types
- doubling
- "no reverb" rules
- delay habits

So the EQ in each patch mostly turns the e935 into the record's mic: an M49's fuller low-mids, an SM7B's darker top, a condenser's air. Anything beyond that is reasoning and is marked as such in the patch note.

**What the box can't do:**
- **Real double-tracking:** a short single echo (25-35 ms) stands in.
- **A moving flanger or chorus:** a fixed comb stands in.
- **A Leslie:** no stand-in, so Planet Caravan and Black Hole Sun are not included.

Researched 2026-11-12, 2026-11-16 and 2026-11-17. Several pages were paywalled or blocked; where a fact came only from a search excerpt, it says so.

| Patch | On record | Sources |
|---|---|---|
| OZZY SABBATH | Raw chain with "no outboard equipment" (Tom Allom); Regent console with almost no midrange; vocals triple-tracked (Greg Price, secondhand) | [Mix](https://www.mixonline.com/?p=101848), [Classic Rock Revisited](https://classicrockrevisited.com/show_interview.php?id=978), [Sound On Sound](https://www.soundonsound.com/techniques/greg-price-mixing-black-sabbath-live), [Loudwire on the tone](https://loudwire.com/ozzy-osbourne-black-sabbath-best-vocals/) |
| OZZY BLIZZARD | Max Norman: "pretty tight vocal doubling on almost everything"; AMS delay + Lexicon 224 on Diary (lower-reliability blog repost) | [Max Norman interview](https://www.hamerfanclub.com/forums/topic/20125-max-norman-interview/), [blog repost](https://defendersofoldschoolmetal.blogspot.com/2025/05/the-recording-of-blizzard-of-ozz-and.html) |
| MAYNARD CLEAN | 10,000 Days: handheld Soundelux tube condenser in foam (Barresi); Fear Inoculum: "Neve/1176 standard setup" | [Mix](https://www.mixonline.com/recording/making-tools-10000-days-365657), [Waves](https://www.waves.com/in-the-studio-with-tool-and-evil-joe-barresi) |
| MAYNARD DIRT | A "highly distorted SM57" taped to the clean mic; Helios strip with "drastic EQ'ing" | [Mix](https://www.mixonline.com/recording/making-tools-10000-days-365657) |
| MAYNARD MEGAPHONE | B-Band "pipe bomb" pickup mics inside a megaphone; the 350 Hz-3.5 kHz band is a guess | [Mix](https://www.mixonline.com/recording/making-tools-10000-days-365657) |
| MAYNARD WHISPER | Undertow: U67 into a UREI 1176 "cranked way up to expose all the little details" (Sylvia Massy) | [Mix](https://www.mixonline.com/recording/gear-stories-sylvia-massy-366164) |
| KYUSS GARCIA | Sound City Neve; Joe Barresi favours dynamics ("57s on everything"), not confirmed for the vocal; vocals overdubbed; Garcia wants the voice "neck-and-neck with the music" | [Vintage King](https://vintageking.com/blog/joe-barresi/), [Yahoo](https://www.yahoo.com/entertainment/kyuss-blues-red-sun-inside-190005019.html), [Songfacts](https://www.songfacts.com/blog/interviews/john-garcia-ex-kyuss) |
| WIZARD OBORN | Space Echo, Copicat and a Neve pre in the band's kit (2017); Dopethrone vocals "heavily manipulated and low in the mix"; the fuzz-pedal story is fan talk | [Psychedelic Baby](https://www.psychedelicbabymag.com/2017/11/electric-wizard-interview-with-jus.html), [Musique Machine](https://www.musiquemachine.com/reviews/reviews_template.php?id=845) |
| STALEY DIRT | Dave Jerden: M49 > API > Summit compressor, no EQ, all vocals tripled, an H3000 "Layne Staley" delay; reverb darkens, delays keep it "hard" | [Gearspace Q&A](https://gearspace.com/board/q-a-with-dave-jerden/1030250-dirt-alice-chains.html) (blocked to the fetcher, quotes via search excerpt) |
| CORNELL | Superunknown: U47 for soft parts, U87 for full power, sung very close and very loud (secondhand) | [Produce Like A Pro](https://producelikeapro.com/blog/capturing-fire-michael-beinhorn-on-recording-chris-cornell/), [Tape Op (Terry Date)](https://tapeop.com/interviews/123/terry-date) |
| ANSELMO | Sterling Winfield: an SM58 in the control room "with Yamaha NS-10s on 11 and him just screaming" | [URM ep. 48](https://urm.academy/ep48-sterling-winfield/) |
| STEELE | Heavy compression (via search excerpt); "flanged-out vocals" on Bloody Kisses | [Revolver](https://www.revolvermag.com/music/type-o-negatives-bloody-kisses-definitive-oral-history) |
| MUSTAINE | Nothing on record: by ear only | [Rust in Peace](https://en.wikipedia.org/wiki/Rust_in_Peace) |
| DANZIG | Nothing on record: by ear only | [Danzig (album)](https://en.wikipedia.org/wiki/Danzig_(album)) |
| COBAIN NEVERMIND | Butch Vig: Neumann U67 into an LA-2A, "a fair amount of compression", maybe a Pultec for a little top; choruses doubled; mix: "tight slap echo, almost a double", a little reverb, mostly dry | [Sound On Sound](https://www.soundonsound.com/people/butcvig-nevermind-garbage), [Live Nirvana sessions](https://www.livenirvana.com/sessions/studio/may-june-1991.php) |
| COBAIN IN UTERO | Albini: EV PL20 + LOMO 19A9 (identified from a session photo), no double-tracking, mostly first takes, a resonant room; compression now and then (search excerpt) | [Grammy.com](https://www.grammy.com/news/steve-albini-auction-3-microphones-used-nirvanas-utero/), [In Utero](https://en.wikipedia.org/wiki/In_Utero), [Rock and Roll Globe](https://rockandrollglobe.com/?p=39102) |
| VEDDER TEN | U87 > Neve 1081 > UREI 1178 > ADR Vocal Stressor; Tim Palmer's mix: EMT plates, Lexicon 224, delays; the band later called the effects overdone | [Mix, Classic Tracks: Even Flow](https://www.mixonline.com/?p=36256) |
| REZNOR CLEAN | Sean Beavan: handheld Beta 58 > Neve > LA-2A, one solid take, "anti-chorus and anti-pitch-shifting" | [Sound On Sound, Closer](https://www.soundonsound.com/techniques/classic-tracks-nine-inch-nails-closer) |
| REZNOR DISTORTED | Beavan: Neve 1073 > LA-3 > gate > more LA-3s "all the way up"; Reznor: the Neve pre and a Zoom 9030 on "all the vocals", blended so the words read | [Sound On Sound](https://www.soundonsound.com/techniques/classic-tracks-nine-inch-nails-closer), [Guitar World 1994](https://www.theninhotline.com/archives/articles/display/555) |
| SERJ TOXICITY / SERJ SOFT | David Schiffman: Shure SM7 for aggressive vocals, AKG C12 for delicate ones, Neve 1073 pres; no vocal compressor named | [Mix 2001](https://www.mixonline.com/recording/system-down-372602) |
| CANTRELL HARMONY | Dave Jerden: all of Jerry's vocals doubled, Layne's tripled, sung to a finished lead (Gearspace, search excerpt only); Jerry's mic not on record | [Gearspace Q&A](https://gearspace.com/board/q-a-with-dave-jerden/1030250-dirt-alice-chains.html), [Rock N Roll Insight](https://www.rocknrollinsight.com/2017/03/digging-for-dirt-how-alice-in-chains.html) |
| ELVIS SUN | Sam Phillips: slapback from a second Ampex 350 fed back through the board, measured at 134-137 ms; ribbon/dynamic mics; very little limiting, no EQ | [Sound On Sound](https://www.soundonsound.com/people/sam-phillips-sun-records), [Joe Bennett on Halmrast 2017](https://joebennett.net/?p=25083), [Sun Studio gear](https://scottymoore.net/studio_sun.html) |
| LENNON ADT | Abbey Road: ADT 8-12 ms, built for Lennon in 1966; U47 + Fairchild 660 (MusicTech, search excerpt only) | [Abbey Road](https://www.abbeyroad.com/news/inside-abbey-road-artificial-double-tracking-2530), [ADT](https://en.wikipedia.org/wiki/Automatic_double_tracking) |
| LENNON TAPE ECHO | Emerick: the vocal mic fed a tape machine turned up until it fed back, heard in his headphones while singing (A Day in the Life); "1950s" for Instant Karma; echo time not on record | [The Beatles Bible](https://www.beatlesbible.com/1967/01/19/recording-a-day-in-the-life/), [johnlennon.com](https://www.johnlennon.com/news/instant-karma-we-all-shine-on/) |
| IAN CURTIS | Mostly secondhand: "down a telephone line" on Insight, AMS DMX 15-80 on She's Lost Control; times not on record | [Vintage King](https://vintageking.com/blog/joy-divisions-unknown-pleasures/), [Unknown Pleasures](https://en.wikipedia.org/wiki/Unknown_Pleasures) |
| CHINO | Terry Date's general habit: a 58 through two 1176s; on Ohms (2020) a RAT and an Echorec-style slap (search excerpt). White Pony's chain not on record | [Everything Recording](https://everythingrecording.com/of-metal-and-immortality/), [The Ringer](https://www.theringer.com/2020/06/17/music/oral-history-deftones-white-pony-20th-anniversary) |
| HOMME LULLABIES | Joe Barresi on Lullabies to Paralyze: "The 1176 is my favourite on vocals", 2-3 mics with one slightly overdriven, dry leads. Rated R / Songs for the Deaf: nothing found | [Sound On Sound](https://www.soundonsound.com/techniques/recording-queens-stone-age) |
| HOMME VILLAINS | Mark Rankin: a clean Sanken CU-51 plus an RCA 44 ribbon "for a bit of character"; Eventide 910/949 Harmonizer in the chain; "a little bit of delay and a little bit of pitch" as a fake room; the room at United recorded (via Equipboard, quoting AudioTechnology, which was blocked) | [Equipboard](https://equipboard.com/albums/queens-of-the-stone-age-villains) |
| HOMME HARSH EV | Rankin: "sometimes an old dynamic like an Electro-Voice", "purposely trying to get a harder, harsh sound" (same source) | [Equipboard](https://equipboard.com/albums/queens-of-the-stone-age-villains) |
| HOMME WHISPER | Barresi: on This Lullaby, whispery background vocals "through a mic with an analogue guitar pedal reverb on it" | [Sound On Sound](https://www.soundonsound.com/techniques/recording-queens-stone-age) |
| HOMME AMP VOCAL | Barresi: vocals "sung through an amplifier or a Leslie cabinet"; on Like a Drug, "Josh singing into an amplifier, and every mic in the room wide open" | [Sound On Sound](https://www.soundonsound.com/techniques/recording-queens-stone-age) |
| HOMME FALSETTO | Nothing found: ...Like Clockwork was recorded by Mark Rankin at Pink Duck, but no vocal chain is published. By ear | [...Like Clockwork](https://en.wikipedia.org/wiki/...Like_Clockwork) |
| WEILAND PICKUP | Dean DeLeo: Scott sang the Dead & Bloated intro into the bridge pickup of Dean's Les Paul | [Dead & Bloated](https://en.wikipedia.org/wiki/Dead_%26_Bloated) |
| WEILAND CORE | Brendan O'Brien: STP vocals got "more echo, more trippy, more psychedelic"; his usual chain is a Neve + LA-2A or Tube-Tech (video transcript) | [Rick Beato interview transcript](https://rosetta.to/u/rickbeato/brendan-o-brien-interview-the-unsung-hero-of-rock-music) |
| LEMMY | Nothing on record for the chain; Vic Maile "got me singing instead of just shouting" | [Ace of Spades](https://en.wikipedia.org/wiki/Ace_of_Spades_(Mot%C3%B6rhead_album)) |
| LANEGAN | Nothing on record for the chain | [Looking back at Sweet Oblivion](https://desperate-living.com/2019/04/19/shadows-of-the-season-looking-back-at-screaming-trees-sweet-oblivion-with-gary-lee-conner/) |
| KORN DAVIS | Gear not on record; Ross Robinson: live takes, accidents kept, 60-70% first takes | [Korn oral history](https://au.rollingstone.com/music/music-news/korns-1994-debut-lp-the-oral-history-of-the-most-important-metal-record-of-the-last-20-years-557/amp/), [Life Is Peachy](https://en.wikipedia.org/wiki/Life_Is_Peachy) |
| CISNEROS CHANT | Chain not on record; Cisneros: the vocals "play riffs", tied to breathing | [Arthur Magazine](https://arthurmag.com/2007/02/05/interview-with-om-from-the-pages-of-arthur-magazine/), [Willamette Week](https://www.wweek.com/portland/article-23901-move-that-dope.html) |
| MATT PIKE | Chain not on record; wanted the vocals "out front" on Snakes for the Divine | [Tufts Daily](https://www.tuftsdaily.com/article/2010/04/high-on-fires-matt-pike-discusses-groups-latest-album) |
| MORRISOUND GROWL | Chain not on record; liner notes say no harmonizer; Scott Burns felt the vocals "sounded heavy on their own" | [Butchered at Birth](https://en.wikipedia.org/wiki/Butchered_at_Birth), [Decibel on Cause of Death](https://decibelmagazine.com/2019/02/06/memories-remain-the-making-of-obituarys-cause-of-death/) |
| HALFORD | Chain not on record; Allom: the room was "small and dead"; the Priest harmony trick (minor third an octave below) via Andy Sneap | [Classic Rock Revisited](https://classicrockrevisited.com/show_interview.php?id=978), [Sound On Sound](https://www.soundonsound.com/techniques/inside-track-judas-priest) |
| DICKINSON | Chain not on record; Martin Birch coached the performance | [Ear of Newt](https://earofnewt.com/2013/08/10/bruce-dickinson-on-martin-birch-the-exit-of-clive-burr-and-the-lobotomization-of-eddie-on-iron-maidens-new-album-piece-of-mind/) |

None of these has been heard through the box yet. They are starting points: tweak them and SAVE AS YOURS.
