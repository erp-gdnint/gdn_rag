"""Text normalization for keyword search.

The same functions are used when we index text and when we read a question,
so both sides always agree. See plan.md section 6 for the reasons:

- MariaDB InnoDB FULLTEXT ignores words shorter than 3 characters.
- It splits "C++" into "C", and "node.js" into "node" and "js".
- It does no stemming ("certificate" does not match "certification").

We fix this with: aliases, a light stemmer, prefix search and a small
REGEXP scan for 1-2 character words.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_SEPARATORS = re.compile(r"[\W_]+", re.UNICODE)
_BOOLEAN_CHARS = re.compile(r'[+\-<>()~*"@]')
_alias_regex_cache: dict[tuple, re.Pattern] = {}

MAX_QUERY_TOKENS = 12


@dataclass
class ParsedQuery:
	boolean: str = ""  # text for MATCH ... AGAINST (... IN BOOLEAN MODE)
	short_terms: list[str] = field(default_factory=list)  # 1-2 character words
	tokens: list[str] = field(default_factory=list)  # normalized words (no stop words)

	@property
	def empty(self) -> bool:
		return not self.boolean and not self.short_terms


def _alias_regex(aliases: dict[str, str]) -> re.Pattern | None:
	if not aliases:
		return None
	key = tuple(sorted(aliases.items()))
	regex = _alias_regex_cache.get(key)
	if regex is None:
		keys = sorted(aliases, key=len, reverse=True)  # longest first: "c++" before "c"
		pattern = r"(?<![0-9a-z])(?:" + "|".join(re.escape(k) for k in keys) + r")(?![0-9a-z])"
		regex = re.compile(pattern)
		_alias_regex_cache[key] = regex
	return regex


def normalize(text: str, aliases: dict[str, str] | None = None) -> str:
	"""Lowercase, replace aliases, and keep only letters and digits separated by single spaces."""
	text = unicodedata.normalize("NFKC", text or "").lower()
	aliases = {k.lower(): v.lower() for k, v in (aliases or {}).items()}
	regex = _alias_regex(aliases)
	if regex:
		text = regex.sub(lambda m: f" {aliases[m.group(0)]} ", text)
	return _SEPARATORS.sub(" ", text).strip()


def build_search_text(content: str, search: dict | None = None) -> str:
	"""Text that goes into the FULLTEXT column."""
	search = search or {}
	text = normalize(content, search.get("aliases"))
	phrases = search.get("alias_phrases") or {}
	if phrases:
		# Two ways, so both spellings find the text:
		#   text says "artificial intelligence" -> also add "artificialintelligence" (the alias of "ai")
		#   text says "ai"                      -> (alias already gives "artificialintelligence") also add the words
		words = set(text.split())
		padded = f" {text} "
		extra: list[str] = []
		for target, phrase in phrases.items():
			target, phrase = target.lower(), phrase.lower()
			has_target = target in words
			has_phrase = f" {phrase} " in padded
			if has_phrase and not has_target:
				extra.append(target)
			elif has_target and not has_phrase:
				extra.append(phrase)
		if extra:
			text = f"{text} {' '.join(extra)}"
	return text


_PLURAL_IES = "ies"
_VERB_SUFFIXES = ("ingly", "edly", "ing", "ed")
# Only long, clear endings. Short ones (-al, -er, -or, -ly, -y, -e) over-stem: "animals" would become
# "anim*" and also match "animation". A prefix search already matches plurals without them.
_DERIVATIONAL = (
	"ational", "ization", "ations", "ation", "ments", "ment", "ness", "ity", "ive", "ize", "ate", "ify",
)  # fmt: skip
_MIN_STEM = 4


def light_stem(word: str) -> str:
	"""A tiny deterministic stemmer. The result is never shorter than 4 characters.

	certificate / certificates / certification -> "certific"
	animals -> "animal"   courses -> "course"   learning -> "learn"
	"""
	original = word
	w = word
	# 1. plurals
	if w.endswith(_PLURAL_IES) and len(w) > 4:
		w = w[:-3] + "y"
	elif w.endswith("sses"):
		w = w[:-2]
	elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
		w = w[:-1]
	# 2. verb endings
	for suffix in _VERB_SUFFIXES:
		if w.endswith(suffix) and len(w) - len(suffix) >= _MIN_STEM:
			w = w[: -len(suffix)]
			break
	# 3. word endings
	for suffix in _DERIVATIONAL:
		if w.endswith(suffix) and len(w) - len(suffix) >= _MIN_STEM:
			w = w[: -len(suffix)]
			break
	return w if len(w) >= _MIN_STEM else original


def tokenize_question(question: str, search: dict | None = None) -> list[str]:
	"""Normalized words of a question without stop words (deduplicated, at most 12)."""
	search = search or {}
	stop = {s.lower() for s in search.get("stopwords", [])}
	seen: list[str] = []
	for token in normalize(question, search.get("aliases")).split():
		if token in stop or token in seen:
			continue
		seen.append(token)
		if len(seen) >= MAX_QUERY_TOKENS:
			break
	return seen


def build_query(question: str, search: dict | None = None) -> ParsedQuery:
	"""Turn a user question into a FULLTEXT BOOLEAN query plus a list of short terms."""
	tokens = tokenize_question(question, search)
	parts: list[str] = []
	short_terms: list[str] = []
	for token in tokens:
		token = _BOOLEAN_CHARS.sub("", token)
		if not token:
			continue
		if len(token) <= 2:
			if re.fullmatch(r"[a-z0-9]{1,2}", token):
				short_terms.append(token)
			continue
		stem = light_stem(token)
		# Use a prefix search when the stem is long enough, or when stemming changed the word.
		if len(stem) >= 5 or (stem != token and len(stem) >= _MIN_STEM):
			parts.append(f"{stem}*")
		else:
			parts.append(token)
	return ParsedQuery(boolean=" ".join(parts), short_terms=short_terms, tokens=tokens)


def title_overlap(tokens: list[str], title: str, aliases: dict[str, str] | None = None) -> float:
	"""Fraction of query tokens that appear (as prefix of a word) in the title. 0.0 to 1.0."""
	if not tokens:
		return 0.0
	title_words = normalize(title, aliases).split()
	found = 0
	for token in tokens:
		stem = light_stem(token) if len(token) > 2 else token
		if any(w.startswith(stem) for w in title_words):
			found += 1
	return found / len(tokens)


def coverage(tokens: list[str], text: str, aliases: dict[str, str] | None = None) -> float:
	"""Fraction (0..1) of the question words that appear in `text`.

	Words of 3+ letters match as a prefix of a word in the text (same rule as the query), so
	"certificate" is found in "certification". 1-2 letter words must match a whole word.
	FULLTEXT relevance rewards how OFTEN a word repeats; this rewards how MANY different words match.
	"""
	if not tokens:
		return 0.0
	words = set(normalize(text, aliases).split())
	found = 0
	for token in tokens:
		if len(token) <= 2:
			found += token in words
		else:
			stem = light_stem(token)
			found += any(w.startswith(stem) for w in words)
	return found / len(tokens)
