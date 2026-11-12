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

Researched 2026-11-12. Several pages were paywalled or blocked; where a fact came only from a search excerpt, it says so.

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
| HETFIELD PUPPETS | Rasmussen: "a Shure SM7B, through the Trident desk and UREI compressor, to tape", double-tracked, "no reverb!" | [Mix](https://www.mixonline.com/recording/classic-track-master-of-puppets-metallica) |
| HETFIELD BLACK | Bob Rock: a Neumann FET mic, a single undoubled vocal | [Mix](https://www.mixonline.com/recording/classic-tracks-metallicas-enter-sandman-366232), [MusicRadar](https://musicradar.com/news/bob-rock-metallica-black-album-interview) |
| ANSELMO | Sterling Winfield: an SM58 in the control room "with Yamaha NS-10s on 11 and him just screaming" | [URM ep. 48](https://urm.academy/ep48-sterling-winfield/) |
| STEELE | Heavy compression (via search excerpt); "flanged-out vocals" on Bloody Kisses | [Revolver](https://www.revolvermag.com/music/type-o-negatives-bloody-kisses-definitive-oral-history) |
| MUSTAINE | Nothing on record: by ear only | [Rust in Peace](https://en.wikipedia.org/wiki/Rust_in_Peace) |
| DANZIG | Nothing on record: by ear only | [Danzig (album)](https://en.wikipedia.org/wiki/Danzig_(album)) |

None of these has been heard through the box yet. They are starting points: tweak them and SAVE AS YOURS.
