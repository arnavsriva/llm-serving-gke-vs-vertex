"""Word lists for synthetic prompts.

Plain, common English keeps the tokens-per-word ratio close to what real prose produces, so a
prompt length given in words maps predictably onto tokens (the server reports the exact count).
"""

_WORDS_TEXT = """
    time year people way day man thing woman life child world school state family student group
    country problem hand part place case week company system program question work government
    number night point home water room mother area money story fact month lot right study book
    eye job word business issue side kind head house service friend father power hour game line
    end member law car city community name president team minute idea kid body information back
    parent face others level office door health person art war history party result change
    morning reason research girl guy moment air teacher force education foot boy age policy
    music market sense nation plan college interest death experience effect class control care
    field development role effort rate heart drug show leader light voice wife police mind price
    report decision son view relationship town road arm difference value building action model
    season society tax director position player record paper space ground form event official
    matter center couple site project activity star table need court oil situation cost industry
    figure street image phone data picture practice piece land product doctor wall patient
    worker news test movie north love support technology step baby computer type attention film
    tree source organization hair window evidence population harvest river garden bridge
    mountain island forest valley ocean harbor village castle engine signal network channel
    memory kernel storage cluster server request answer method theory
"""
WORDS: tuple[str, ...] = tuple(_WORDS_TEXT.split())

TOPICS: tuple[str, ...] = (
    "the history of bridges",
    "how cities grow",
    "the economics of farming",
    "ocean currents",
    "the design of programming languages",
    "the life of honeybees",
    "public libraries",
    "the invention of the printing press",
    "mountain weather",
    "how vaccines are developed",
    "the future of public transport",
    "volcanoes",
    "the role of music in culture",
    "renewable energy",
    "the architecture of cathedrals",
    "how computers store data",
    "the migration of birds",
    "the rules of chess",
    "coffee",
    "the exploration of Antarctica",
    "the science of sleep",
    "ancient trade routes",
    "how rivers shape valleys",
    "the history of photography",
)
