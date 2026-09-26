"""CU Route Assistant — grounded AI information and routing assistant.

A Retrieval-Augmented Generation system over Cosmopolitan University's own
published pages. It answers questions with a citation for every factual claim
and routes users to the correct portal, office, or contact.

Design commitments, all of which are enforced in code rather than in prose:

1.  Abstention. If the corpus does not support an answer, the system says so.
    It never supplies a figure, date, or policy that the University has not
    published. This is the primary harm the product exists to avoid.
2.  Citation or silence. A factual claim without a resolvable source is
    suppressed.
3.  Degrade, never break. A neural embedder, a lexical embedder, and a language
    model are each optional. Removing any one of them degrades the answer
    quality; removing all of them still leaves a working, useful product.
4.  Read-only and account-free. No personal data is collected, no credentials
    are accepted, and no action is performed on any external system.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
