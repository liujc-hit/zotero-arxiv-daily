"""Contract tests for the immutable built-in OpenAlex venue catalog."""

# noqa: SIZE_OK - the exact immutable 130-journal contract is intentionally data-heavy.

import re
from dataclasses import FrozenInstanceError

import pytest

from zotero_arxiv_daily.retriever.openalex_venue_catalog import (
    ALL_VENUES,
    CONFERENCE_VENUES,
    JOURNAL_VENUES,
    UNAVAILABLE_OPENALEX_JOURNALS,
    UnavailableVenue,
    VenueSpec,
)


EXPECTED_JOURNAL_NAMES = frozenset(
    {
        "3D Printing and Additive Manufacturing",
        "Actuators (MDPI)",
        "Advanced Robotics",
        "Advanced Robotics Research",
        "Advances in Mechanical Engineering",
        "Aerospace (MDPI)",
        "AI for Engineering (MDPI)",
        "Annual Review of Control, Robotics, and Autonomous Systems",
        "Annual Reviews in Control",
        "Annual Reviews of Heat Transfer",
        "Archives of Civil and Mechanical Engineering",
        "Artificial Intelligence in Agriculture",
        "ASME Journal of Autonomous Vehicles and Systems",
        "ASME Journal of Biomechanical Engineering",
        "ASME Journal of Fluids Engineering",
        "ASME Journal of Heat and Mass Transfer",
        "ASME Journal of Manufacturing Science and Engineering",
        "ASME Journal of Mechanical Design",
        "ASME Journal of Micro and Nano-Manufacturing",
        "ASME Journal of Thermal Science and Engineering Applications",
        "ASME Journal of Tribology",
        "ASME Letters in Dynamic Systems and Control",
        "Assembly Automation",
        "Automatica",
        "Automation (MDPI)",
        "Autonomous Robots",
        "Biomimetics",
        "Bioinspiration & Biomimetics",
        "Chinese Journal of Mechanical Engineering (CJME)",
        "Cogent Engineering",
        "Computational Mechanics",
        "Control Engineering Practice",
        "Cyborg and Bionic Systems",
        "Data-Centric Engineering",
        "Discover Robotics",
        "Drones (MDPI)",
        "European Journal of Control",
        "Frontiers in Manufacturing Technology",
        "Frontiers in Mechanical Engineering",
        "Frontiers in Neurorobotics",
        "Frontiers in Robotics and AI",
        "Heliyon",
        "IEEE Access",
        "IEEE Robotics & Automation Magazine (RAM)",
        "IEEE Robotics and Automation Letters (RA-L)",
        "IEEE Robotics and Automation Practice (RAP)",
        "IEEE Transactions on Automation Science and Engineering (T-ASE)",
        "IEEE Transactions on Field Robotics (T-FR)",
        "IEEE Transactions on Haptics",
        "IEEE Transactions on Intelligent Vehicles",
        "IEEE Transactions on Medical Robotics and Bionics",
        "IEEE Transactions on Robot Learning",
        "IEEE Transactions on Robotics (T-RO)",
        "IEEE/ASME Journal of Microelectromechanical Systems (JMEMS)",
        "IET Collaborative Intelligent Manufacturing",
        "IET Control Theory and Applications",
        "IET Cyber-systems and Robotics",
        "Industrial Robot",
        "Industries (MDPI)",
        "Intelligent Service Robotics",
        "International Journal of Advanced Manufacturing Technology",
        "International Journal of Advanced Robotic Systems (IJARS)",
        "International Journal of Heat and Mass Transfer",
        "International Journal of Intelligent Robotics and Applications",
        "International Journal of Mechanical System Dynamics",
        "International Journal of Social Robotics",
        "Iranian Journal of Science and Technology - Trans. of Mechanical Engineering",
        "Journal of Advanced Mechanical Design, Systems, and Manufacturing",
        "Journal of Bionic Engineering",
        "Journal of Field Robotics",
        "Journal of Intelligent and Robotic Systems (JINT)",
        "Journal of Intelligent Manufacturing",
        "Journal of Manufacturing and Materials Processing (MDPI)",
        "Journal of Manufacturing Systems",
        "Journal of Materials Processing Technology",
        "Journal of Mechanisms and Robotics (JMR)",
        "Journal of Robotics",
        "Journal of the Brazilian Society of Mechanical Sciences and Engineering",
        "Machines (MDPI)",
        "Mechanical Sciences (Copernicus)",
        "Mechanism and Machine Theory",
        "Mechatronics",
        "Nature Communications",
        "npj Robotics",
        "PeerJ",
        "PLOS ONE",
        "Proceedings of the IMechE Part A: J. Power and Energy",
        "Proceedings of the IMechE Part B: J. Engineering Manufacture",
        "Proceedings of the IMechE Part C: J. Mechanical Engineering Science",
        "Proceedings of the IMechE Part D: J. Automobile Engineering",
        "Proceedings of the IMechE Part E: J. Process Mechanical Engineering",
        "Proceedings of the IMechE Part F: J. Rail and Rapid Transit",
        "Proceedings of the IMechE Part G: J. Aerospace Engineering",
        "Proceedings of the IMechE Part H: J. Engineering in Medicine",
        "Proceedings of the IMechE Part I: J. Systems and Control Engineering",
        "Proceedings of the IMechE Part J: J. Engineering Tribology",
        "Proceedings of the IMechE Part K: J. Multi-body Dynamics",
        "Proceedings of the IMechE Part L: J. Materials: Design and Applications",
        "Proceedings of the IMechE Part M: J. Engineering for the Maritime Environment",
        "Proceedings of the IMechE Part N: J. Nanomaterials, Nanoengineering and Nanosystems",
        "Proceedings of the IMechE Part O: J. Risk and Reliability",
        "Proceedings of the IMechE Part P: J. Sports Engineering and Technology",
        "ROBOMECH Journal",
        "Robotica",
        "Robotics",
        "Robotics and Autonomous Systems",
        "Robotics and Computer-Integrated Manufacturing",
        "Robotics Reports",
        "SAE International Journal of Advances and Current Practices in Mobility",
        "SAE International Journal of Aerospace",
        "SAE International Journal of Connected and Automated Vehicles",
        "SAE International Journal of Electrified Vehicles",
        "SAE International Journal of Engines",
        "SAE International Journal of Materials and Manufacturing",
        "SAE International Journal of Passenger Vehicle Systems",
        "SAE International Journal of Sustainable Transportation, Energy, Environment, & Policy",
        "SAE International Journal of Transportation Cybersecurity and Privacy",
        "SAE International Journal of Transportation Safety",
        "SAE International Journal of Vehicle Dynamics, Stability, and NVH",
        "Science Robotics",
        "Scientific Reports",
        "Sensors (MDPI)",
        "Sensors and Actuators A: Physical",
        "Sensors and Actuators B: Chemical",
        "Soft Robotics",
        "Technologies (MDPI)",
        "The International Journal of Robotics Research (IJRR)",
        "Tribology International",
        "Vehicle System Dynamics",
        "Wearable Technologies",
    }
)

EXPECTED_CONFERENCE_NAMES = frozenset(
    {
        "ACM/IEEE HRI",
        "AIM",
        "ARSO",
        "BioRob",
        "CoRL",
        "DARS",
        "FSR",
        "Humanoids",
        "ICAR",
        "ICARCV",
        "ICORR",
        "ICRA",
        "ICUAS",
        "IEEE CASE",
        "IEEE Haptics Symposium",
        "IROS",
        "ISARC",
        "ISER",
        "ISRR",
        "Living Machines",
        "MRS",
        "RO-MAN",
        "ROBIO",
        "RoboSoft",
        "RSS",
        "SSRR",
        "SYROCO",
        "World Haptics",
    }
)

FORBIDDEN_CONFERENCE_NAMES = frozenset(
    {
        "ACRA",
        "CBS",
        "CLAWAR",
        "ERAS",
        "i-SAIRAS",
        "MICCAI",
        "RAAD",
        "RoboCup Symposium",
        "TAROS",
        "WAFR",
    }
)

EXPECTED_UNAVAILABLE_CONFERENCE_NAMES = frozenset(
    {"ACM/IEEE HRI", "Humanoids", "ISRR", "MRS", "RoboSoft", "SYROCO"}
)

OPENALEX_ID_PATTERN = re.compile(r"S[1-9][0-9]*\Z")
ISSN_PATTERN = re.compile(r"[0-9]{4}-[0-9]{3}[0-9X]\Z")


def test_journal_scope_is_exact() -> None:
    # Given the active and explicitly unavailable journal records
    # When their logical inventory is combined
    actual_names = {venue.display_name for venue in JOURNAL_VENUES} | {
        venue.display_name for venue in UNAVAILABLE_OPENALEX_JOURNALS
    }

    # Then it equals every unique section 1-14 journal plus MDPI Robotics
    assert actual_names == EXPECTED_JOURNAL_NAMES


def test_conference_scope_is_exact() -> None:
    # Given the built-in conference records
    # When their display names are collected
    actual_names = {venue.display_name for venue in CONFERENCE_VENUES}

    # Then only the confirmed default conference set is present
    assert actual_names == EXPECTED_CONFERENCE_NAMES


def test_forbidden_conferences_are_absent() -> None:
    # Given the built-in conference records
    # When their display names are compared with optional/watch venues
    actual_names = {venue.display_name for venue in CONFERENCE_VENUES}

    # Then no optional/watch venue is present
    assert actual_names.isdisjoint(FORBIDDEN_CONFERENCE_NAMES)


def test_exports_are_immutable_tuples() -> None:
    # Given every public catalog collection
    collections = (
        JOURNAL_VENUES,
        CONFERENCE_VENUES,
        UNAVAILABLE_OPENALEX_JOURNALS,
        ALL_VENUES,
    )

    # When their runtime container types and composition are observed
    collection_types = tuple(type(collection) for collection in collections)

    # Then all public collections are immutable tuples
    assert collection_types == (tuple, tuple, tuple, tuple)


def test_records_are_frozen_and_slotted() -> None:
    # Given representative records of both public record types
    venue = VenueSpec(
        key="example",
        display_name="Example",
        source_type="journal",
        openalex_source_ids=("S1",),
        issns=(),
    )
    unavailable = UnavailableVenue(
        key="unavailable_example",
        display_name="Unavailable Example",
        source_type="journal",
        reason="No stable OpenAlex source exists.",
    )

    # When mutation and instance dictionaries are attempted
    with pytest.raises(FrozenInstanceError):
        setattr(venue, "key", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(unavailable, "reason", "changed")

    # Then frozen assignment fails and slots suppress per-instance dictionaries
    assert not hasattr(venue, "__dict__")
    assert not hasattr(unavailable, "__dict__")


def test_keys_are_unique_across_active_and_unavailable_records() -> None:
    # Given every active and unavailable logical venue record
    records = (
        JOURNAL_VENUES
        + CONFERENCE_VENUES
        + UNAVAILABLE_OPENALEX_JOURNALS
    )

    # When all logical keys are collected
    keys = tuple(venue.key for venue in records)

    # Then no logical venue is represented twice
    assert len(keys) == len(set(keys))


def test_every_active_venue_has_a_stable_identifier() -> None:
    # Given every active venue
    # When identifier tuples are inspected
    missing = tuple(
        venue.key
        for venue in ALL_VENUES
        if not venue.openalex_source_ids and not venue.issns
    )

    # Then every active venue has an exact OpenAlex ID or normalized ISSN
    assert missing == ()


def test_identifiers_are_normalized_and_globally_unique() -> None:
    # Given all exact identifiers in the active catalog
    source_ids = tuple(
        source_id
        for venue in ALL_VENUES
        for source_id in venue.openalex_source_ids
    )
    issns = tuple(issn for venue in ALL_VENUES for issn in venue.issns)

    # When their syntax and cardinality are checked
    source_ids_are_normalized = all(
        OPENALEX_ID_PATTERN.fullmatch(source_id) for source_id in source_ids
    )
    issns_are_normalized = all(ISSN_PATTERN.fullmatch(issn) for issn in issns)

    # Then identifiers are canonical literals and never identify two venues
    assert source_ids_are_normalized
    assert issns_are_normalized
    assert len(source_ids) == len(set(source_ids))
    assert len(issns) == len(set(issns))


def test_source_types_match_catalog_partitions() -> None:
    # Given the journal, conference, and unavailable partitions
    # When their source types are collected
    journal_types = {venue.source_type for venue in JOURNAL_VENUES}
    conference_types = {venue.source_type for venue in CONFERENCE_VENUES}
    unavailable_types = {
        venue.source_type for venue in UNAVAILABLE_OPENALEX_JOURNALS
    }

    # Then only the two supported logical source types are represented
    assert journal_types == {"journal"}
    assert conference_types == {"conference"}
    assert unavailable_types <= {"journal"}


def test_unavailable_journals_have_factual_reasons() -> None:
    # Given every journal that lacks a stable OpenAlex source
    # When its reason is normalized
    reasons = tuple(
        venue.reason.strip() for venue in UNAVAILABLE_OPENALEX_JOURNALS
    )

    # Then no unavailable record is silently or vaguely omitted
    assert all(reasons)


def test_unavailable_conference_blockers_are_explicit() -> None:
    # Given conference records that cannot be queried by an exact source
    unavailable: dict[str, str] = {}
    for venue in CONFERENCE_VENUES:
        match venue:  # noqa: MATCH_OK - VenueSpec | UnavailableVenue is exhaustive.
            case UnavailableVenue(display_name=display_name, reason=reason):
                unavailable[display_name] = reason.strip()
            case VenueSpec():
                pass

    # When their logical names and reasons are inspected
    unavailable_names = frozenset(unavailable)

    # Then only the six verified OpenAlex modeling blockers are inactive
    assert unavailable_names == EXPECTED_UNAVAILABLE_CONFERENCE_NAMES
    assert all(unavailable.values())
