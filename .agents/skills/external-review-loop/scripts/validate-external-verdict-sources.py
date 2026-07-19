#!/usr/bin/env python3
"""Fail closed unless every external-review issue has an approved source."""

import json
import sys


ALLOWED_SOURCES = frozenset({"agy", "gemini", "re-review"})
ALLOWED_VERDICTS = frozenset(
    {"confirmed", "partial", "deferred", "rejected", "duplicate"}
)


class DuplicateKeyError(ValueError):
    pass


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON key: {0}".format(key))
        result[key] = value
    return result


def fail(message):
    print("INVALID external verdict ledger: {0}".format(message), file=sys.stderr)
    return 1


def main(argv):
    if len(argv) != 2:
        print("usage: validate-external-verdict-sources.py <verdicts.json|->", file=sys.stderr)
        return 2

    source_path = argv[1]
    try:
        if source_path == "-":
            document = json.load(sys.stdin, object_pairs_hook=reject_duplicate_keys)
        else:
            with open(source_path, "r", encoding="utf-8") as source_file:
                document = json.load(source_file, object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as error:
        return fail(str(error))

    if not isinstance(document, dict):
        return fail("top level must be an object")
    if document.get("loop") != "external-review":
        return fail("loop must equal 'external-review'")

    issues = document.get("issues")
    if not isinstance(issues, list):
        return fail("issues must be present as an array")

    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            return fail("issues[{0}] must be an object".format(index))

        fingerprint = issue.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            return fail("issues[{0}].fingerprint must be a non-empty string".format(index))

        verdict = issue.get("verdict")
        if not isinstance(verdict, str) or verdict not in ALLOWED_VERDICTS:
            return fail(
                "issues[{0}].verdict={1!r}; allowed values are confirmed, partial, "
                "deferred, rejected, duplicate".format(index, verdict)
            )

        round_number = issue.get("round")
        if (
            isinstance(round_number, bool)
            or not isinstance(round_number, int)
            or round_number < 1
        ):
            return fail("issues[{0}].round must be an integer >= 1".format(index))

        source = issue.get("source")
        if not isinstance(source, str) or source not in ALLOWED_SOURCES:
            return fail(
                "issues[{0}].source={1!r}; allowed values are agy, gemini, re-review".format(
                    index, source
                )
            )

    print("VALID external verdict ledger: {0} issue(s)".format(len(issues)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
