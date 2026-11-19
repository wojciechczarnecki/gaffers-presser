# Set v4: the contested cases for your review (SPEC 022, AC6)

The session re-labelled all 44 unreviewed cases of set v4 against the v7 rule (reading each post). 34 of them are settled by the session (`review_source: session`). These 10 depend on a judgement the post leaves open, so they stay unreviewed until you run `review` on them. In `review`: `a` accepts the shown label (it becomes `review_source: owner`), `e` edits it.

Not contested, labelled with no event by the session: 2116188146660409531 ("What different reasons fam?"), 2116265028458315935 ("Jackson owners what do you think?") and 2116174660693836212 ("I'm probably going to still buy Jackson") are reactions of FPL managers with no news in them; 2116448660980732008 is the tips post (no event, as you fixed). Kroupi in 2115364828453728573 (Rose before the cup tie) is no event because it speaks of cup minutes.

Run from `backend/`.

## 1. 2115825835525488887 (FPLFocal, test)

Post:

> 🗣️ Rose says Kroupi isn't ready to start just yet, needs to build up his minutes first. Scott hasn't trained with the squad yet but might in the next few days, could be back GW10 if not GW11 is likely. https://t.co/6ALuxwRN35

- **Session's label:** Kroupi `benched`/confirmed (Rose, "not ready to start"); Scott `doubt`/confirmed.
- **Why it is contested:** Two calls. (1) Kroupi: v7 gives no event for a rest, and "not ready to start" says he is fit but not starting, so `benched`; the owner already fixed this for the case. (2) Scott has not trained with the squad and "could be back GW10 if not GW11 is likely": he almost surely misses GW9, so `out` is arguable, but the post leaves GW10 open, so I used `doubt`. The same Scott call applies to 2115802087837540454 (BenDinnery, `doubt`/likely) and 2115806043523395847 (FFScout, `doubt`/confirmed): if you change it here, tell me and I change those two.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2115825835525488887`

## 2. 2115481335171108991 (FFScout, dev)

Post:

> Calafiori + Rice injury updates as Arsenal crash out 🚨 
> 
> ➕ Plus, details of a free midweek which may be good news for FPL managers! 
> 
> EFL Cup Scout Notes 👇 
> https://t.co/Zx1j548xYl

- **Session's label:** Calafiori `doubt`/likely, Rice `doubt`/likely.
- **Why it is contested:** The post is only a headline for an article behind a link: "injury updates". It names both as injured but gives no status, so any label is a guess about the article. `doubt` is the weakest claim that fits "injury updates"; the alternative is no event, because the text itself says nothing about availability.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2115481335171108991`

## 3. 2116153319949226164 (BenDinnery, test)

Post:

> [..] #EFC 
> 
> ICYMI
> 
> On the absence of Jack Grealish following a missed training session in the build-up to the Newcastle cup game.
> 
> 'Well, you probably know I'm not going to give any details, and it's a club decision. So, I support the decision, but it's a club decision...'
> 
> 'It's something which I don't really want to speak too much about. [I support the] decision completely, yeah.'
> 
> On whether Grealish could return on Monday... 
> 
> 'I wouldn't give any team news away.'

- **Session's label:** Grealish `doubt`/confirmed (tag `other_match_absence` removed).
- **Why it is contested:** He missed a training session before the cup game, a club decision with no injury given (a rest-like absence under v7). But the manager is asked whether he could return on Monday and refuses to say, which leaves his next Premier League match open. I chose `doubt` (the same call as FFScout 2116153715967090742, "Moyes wouldn't confirm if Grealish was available", `doubt`/confirmed) and dropped the tag, because the set rule says an `other_match_absence` case has no `out`/`doubt`. The alternative is no event, keeping the tag (then 2116153715967090742 also becomes no Grealish event).
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116153319949226164`

## 4. 2116173460590244138 (BenDinnery, test)

Post:

> 🎙️ Andoni Iraola. #LFC 
> 
> Team news...
> 
> 'I think (Dominik) Szoboszlai and (Victor) Munoz have a chance, but we still have two days. I think they have a chance, yes.'
> 
> Florian Wirtz featured from the bench on Tuesday night as he continues to manage a minor ankle issue... The Reds made seven changes for the Carabao Cup tie against Chelsea.
> 
> Alisson will miss the upcoming internationals, but Carlo Ancelotti confirms his absence is not injury-related...
> 
> 'We don’t need to test Alisson, we know him very well. He is the best goalkeeper. When the Copa America begins, he will be called up, and it’s the same for the others [experienced players]. Right now, it’s time to observe, refresh the squad, and help young players reach their full potential.'
> 
> https://t.co/9L4lFJifn2

- **Session's label:** Szoboszlai `doubt`/confirmed, Munoz `doubt`/confirmed; no event for Wirtz or Alisson (tag removed).
- **Why it is contested:** Szoboszlai and Munoz "have a chance" is a clear `doubt`. Contested: Wirtz "continues to manage a minor ankle issue" but played in the cup, with nothing on the next match, so I gave no event (an alternative is `doubt`). Alisson's absence from the internationals is called not injury-related, so no event. The tag is removed because the case has `doubt` events.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116173460590244138`

## 5. 2116110028713164836 (BenDinnery, test)

Post:

> 🎙️ Alvaro Arbeloa. #FFC 
> 
> Team news... 
> 
> Ryan Sessegnon serves the final game of his three-match ban. 
> 
> Smith Rowe has missed the last three following an injury in training, while David Affengruber was absent from the squad midweek after feeling 'some pain' at Coventry. 'Nothing serious,' and the Arbeloa reports no injuries. 
> 
> On the loss to Palace in the Carabao Cup...
> 
> 'It was a tough defeat because we wanted to win this game, the competition was so important for us. We controlled the game, the possession, we could have created more chances. It's something we need to improve, to work on. We need to be more clinical...'
> 
> https://t.co/9L4lFJifn2

- **Session's label:** Sessegnon `out`/confirmed (ban), Affengruber `available`/confirmed; no event for Smith Rowe.
- **Why it is contested:** Smith Rowe "has missed the last three following an injury in training" is a recount, and the post gives no return, but the manager reports no injuries. I gave no event (the pre-label said `available`/confirmed). Affengruber's "nothing serious" gives `available`. Sessegnon's ban: the post says he serves the final game of it, and FFScout says he is suspended for the weekend.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116110028713164836`

## 6. 2116166387291070901 (FPL__Raptor, dev)

Post:

> Said this about Calafiori an hour ago.
> 
> Whenever Arteta is positive about a player’s fitness I assume that is deliberate mind games.
> 
> That being said, it’s hard to bench Calafiori when he has been so brilliant this season and has supposedly trained. https://t.co/KG2Xw3fgex

- **Session's label:** No event.
- **Why it is contested:** An FPL manager weighing a benching decision, who calls Arteta's fitness words "mind games" and says Calafiori "has supposedly trained". The rule for managers writing about their own team gives no event; but it also relays a training report. The pre-label said `available`/likely.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116166387291070901`

## 7. 2116494032461238736 (BenDinnery, test)

Post:

> #CFC 
> 
> Cole Palmer starts after serving his ban midweek… 
> 
> Wesley Fofana comes in for the injured Maxence Lacroix, while Marco Palestra is also recalled with Jorrel Hato sidelined.
> 
> Roméo Lavia is in for the absent Jordan Henderson and Estêvão gets his chance in place of Geovany Quenda.
> 
> #MUFC 
> 
> Lisandro Martínez is preferred to Ayden Heaven, and ©️ Bruno Fernandes is back as Youri Tielemans makes way. 
> 
> Matheus Cunha for Mason Mount is the other change.

- **Session's label:** Line-up: Palmer, Fofana, Palestra, Lavia, Estevao, L.Martinez, Bruno Fernandes, Cunha `confirmed_starter`/confirmed; Lacroix, Hato, Henderson `out`/confirmed; no event for Mount, Quenda, Heaven, Tielemans.
- **Why it is contested:** Henderson is "absent" with no reason: in a line-up post he may be on the bench or out of the squad. I used `out` (as "not in the squad" with no reason is `out`); `benched` or no event is the alternative. Players who make way (Mount, Quenda, Heaven, Tielemans) get no event because the post does not say bench or out.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116494032461238736`

## 8. 2116162085532938410 (BenDinnery, dev)

Post:

> 🎙️ Enzo Maresca. #MCFC 
> 
> Team news... 
> 
> 'They are all available except Antoine Semenyo.' That should mean a return for Matheus Nunes. 
> 
> Erling Haaland, Ruben Dias, Elliot Anderson, Donnarumma and Josko Gvardiol were all given some time off this week...  
> 
> 'After Villa, we gave five players three days off... Go away on holiday. Three days off, go, come back. Full of energy.'
> 
> https://t.co/9L4lFJifn2

- **Session's label:** Semenyo `out`/confirmed, Nunes `available`/likely; no event for the players given days off.
- **Why it is contested:** "That should mean a return for Matheus Nunes": the pre-label said `confirmed_starter`/likely. The post says he returns, not that he starts, and the rule keeps line-up events for named line-ups, so I used `available`. Haaland, Dias and the others were given time off, a workload remark: no event.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116162085532938410`

## 9. 2116150477431672942 (FPLMattW, dev)

Post:

> RT @FPLMeerkat: GOALS CONCEDED THREAD - GAMEWEEK 9
> 
> Welcome back! 😁
> 
> Giving you positional data to highlight those potential differentials 🔎
> 
> An important week as many of you might be on a Free Hit! 🎲
> 
> Starting with:
> 
> 🔵 CHELSEA vs MAN UNITED 👹
> 
> On paper there's 2 names that are standing out to me, ROGERS and MBEUMO. With Joao Pedro injured Rogers is occupying positions furthest up the pitch, which has benefited against Man United, and similar can be said against Chelsea too
> 
> Chelsea are 1st and Man United are 2nd for set piece goals conceded, target those aerial threats!

- **Session's label:** No event.
- **Why it is contested:** A goals-conceded stats thread (FPLMeerkat, reposted) that says in passing "with Joao Pedro injured". The pre-label gave Joao Pedro `out`/confirmed. I gave no event: the post is analysis of fixtures, not an injury report. The alternative is `out`, if you count a passing statement of a known injury.
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2116150477431672942`

## 10. 2115460001162510684 (FPL_Harry, test)

Post:

> For those waiting on Calafiori news... #FPL
> 
> We will hear absolutely nothing on it.
> He won't be in training photos regardless of if he is ok.
> Arteta won't tell us but also won't rule him out.
> 
> He must have had some sort of issue given he did not start Everton and went down the tunnel but we have no idea what it was.
> 
> The fact Arteta said he was injured after the Cup defeat means nothing to me, he basically had to say that or it looks bad resting key players and losing.
> 
> Its a complete guess. I imagine he plays 🤷‍♂️

- **Session's label:** Calafiori `doubt`/rumour.
- **Why it is contested:** The author guesses ("a complete guess, I imagine he plays"), says Arteta won't rule him out and that Arteta said he was injured after the cup defeat. The pre-label said `doubt`/likely; I lowered the certainty to `rumour` because the author reports nothing confirmed. The alternative is no event (a pundit's guess).
- **Command:** `uv run python -m app.extraction.evaluation review --set evals/extraction/v4 --id 2115460001162510684`
