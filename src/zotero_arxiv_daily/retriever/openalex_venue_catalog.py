"""Immutable built-in OpenAlex venue identifiers."""

from dataclasses import dataclass
from typing import Final, Literal


type SourceType = Literal["journal", "conference"]

# source_type is the logical catalog partition, not necessarily OpenAlex's
# imperfect source classification for an exact identifier.


@dataclass(frozen=True, slots=True)
class VenueSpec:
    """An active venue addressable by exact OpenAlex identifiers."""

    key: str
    display_name: str
    source_type: SourceType
    openalex_source_ids: tuple[str, ...]
    issns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnavailableVenue:
    """A confirmed venue without a deterministic OpenAlex source."""

    key: str
    display_name: str
    source_type: SourceType
    reason: str


def _journal(key: str, display_name: str, issn: str) -> VenueSpec:
    return VenueSpec(key, display_name, "journal", (), (issn,))


def _conference_issn(key: str, display_name: str, issn: str) -> VenueSpec:
    return VenueSpec(key, display_name, "conference", (), (issn,))


def _conference_sources(
    key: str,
    display_name: str,
    source_ids: tuple[str, ...],
) -> VenueSpec:
    return VenueSpec(key, display_name, "conference", source_ids, ())


JOURNAL_VENUES: Final[tuple[VenueSpec, ...]] = (
    _journal("ieee_t_ro", "IEEE Transactions on Robotics (T-RO)", "1552-3098"),
    _journal("ijrr", "The International Journal of Robotics Research (IJRR)", "0278-3649"),
    _journal("robotics_autonomous_systems", "Robotics and Autonomous Systems", "0921-8890"),
    _journal("ieee_ra_l", "IEEE Robotics and Automation Letters (RA-L)", "2377-3766"),
    _journal("journal_field_robotics", "Journal of Field Robotics", "1556-4959"),
    _journal("ieee_t_fr", "IEEE Transactions on Field Robotics (T-FR)", "3071-4397"),
    _journal("advanced_robotics", "Advanced Robotics", "0169-1864"),
    _journal("robotica", "Robotica", "0263-5747"),
    _journal("journal_mechanisms_robotics", "Journal of Mechanisms and Robotics (JMR)", "1942-4302"),
    _journal("ijars", "International Journal of Advanced Robotic Systems (IJARS)", "1729-8806"),
    _journal("journal_robotics", "Journal of Robotics", "1687-9600"),
    _journal("intelligent_robotics_applications", "International Journal of Intelligent Robotics and Applications", "2366-5971"),
    _journal("discover_robotics", "Discover Robotics", "3059-3204"),
    _journal("advanced_robotics_research", "Advanced Robotics Research", "2943-9973"),
    _journal("npj_robotics", "npj Robotics", "2731-4278"),
    _journal("robotics_reports", "Robotics Reports", "2835-0111"),
    _journal("ieee_ram", "IEEE Robotics & Automation Magazine (RAM)", "1070-9932"),
    _journal("ieee_rap", "IEEE Robotics and Automation Practice (RAP)", "2995-4304"),
    _journal("autonomous_robots", "Autonomous Robots", "0929-5593"),
    _journal("soft_robotics", "Soft Robotics", "2169-5172"),
    _journal("social_robotics", "International Journal of Social Robotics", "1875-4791"),
    _journal("intelligent_service_robotics", "Intelligent Service Robotics", "1861-2776"),
    _journal("robomech", "ROBOMECH Journal", "2197-4225"),
    _journal("jint", "Journal of Intelligent and Robotic Systems (JINT)", "0921-0296"),
    _journal("frontiers_robotics_ai", "Frontiers in Robotics and AI", "2296-9144"),
    _journal("frontiers_neurorobotics", "Frontiers in Neurorobotics", "1662-5218"),
    _journal("ieee_tmrb", "IEEE Transactions on Medical Robotics and Bionics", "2576-3202"),
    _journal("ieee_haptics", "IEEE Transactions on Haptics", "1939-1412"),
    _journal("wearable_technologies", "Wearable Technologies", "2631-7176"),
    _journal("ieee_robot_learning", "IEEE Transactions on Robot Learning", "3070-1120"),
    _journal("ai_agriculture", "Artificial Intelligence in Agriculture", "2589-7217"),
    _journal("automatica", "Automatica", "0005-1098"),
    _journal("control_engineering_practice", "Control Engineering Practice", "0967-0661"),
    _journal("annual_reviews_control", "Annual Reviews in Control", "1367-5788"),
    _journal("annual_review_cras", "Annual Review of Control, Robotics, and Autonomous Systems", "2573-5144"),
    _journal("european_j_control", "European Journal of Control", "0947-3580"),
    _journal("imeche_i", "Proceedings of the IMechE Part I: J. Systems and Control Engineering", "0959-6518"),
    _journal("iet_control", "IET Control Theory and Applications", "1751-8644"),
    _journal("iet_cyber", "IET Cyber-systems and Robotics", "2631-6315"),
    _journal("rcim", "Robotics and Computer-Integrated Manufacturing", "0736-5845"),
    _journal("manufacturing_systems", "Journal of Manufacturing Systems", "0278-6125"),
    _journal("advanced_manufacturing_technology", "International Journal of Advanced Manufacturing Technology", "0268-3768"),
    _journal("asme_jmse", "ASME Journal of Manufacturing Science and Engineering", "1087-1357"),
    _journal("asme_letters_dsc", "ASME Letters in Dynamic Systems and Control", "2689-6117"),
    _journal("industrial_robot", "Industrial Robot", "0143-991X"),
    _journal("assembly_automation", "Assembly Automation", "0144-5154"),
    _journal("intelligent_manufacturing", "Journal of Intelligent Manufacturing", "0956-5515"),
    _journal("ieee_tase", "IEEE Transactions on Automation Science and Engineering (T-ASE)", "1545-5955"),
    _journal("jmmp", "Journal of Manufacturing and Materials Processing (MDPI)", "2504-4494"),
    _journal("machines", "Machines (MDPI)", "2075-1702"),
    _journal("automation_mdpi", "Automation (MDPI)", "2673-4052"),
    _journal("drones", "Drones (MDPI)", "2504-446X"),
    _journal("technologies", "Technologies (MDPI)", "2227-7080"),
    _journal("industries", "Industries (MDPI)", "3042-9021"),
    _journal("ai_engineering", "AI for Engineering (MDPI)", "3042-8831"),
    _journal("frontiers_manufacturing", "Frontiers in Manufacturing Technology", "2813-0359"),
    _journal("iet_collaborative", "IET Collaborative Intelligent Manufacturing", "2516-8398"),
    _journal("printing_additive_manufacturing", "3D Printing and Additive Manufacturing", "2329-7662"),
    _journal("imeche_b", "Proceedings of the IMechE Part B: J. Engineering Manufacture", "0954-4054"),
    _journal("cogent_engineering", "Cogent Engineering", "2331-1916"),
    _journal("materials_processing", "Journal of Materials Processing Technology", "0924-0136"),
    _journal("asme_micro_nano", "ASME Journal of Micro and Nano-Manufacturing", "2166-0468"),
    _journal("advanced_mechanical_design", "Journal of Advanced Mechanical Design, Systems, and Manufacturing", "1881-3054"),
    _journal("mechanism_machine_theory", "Mechanism and Machine Theory", "0094-114X"),
    _journal("mechatronics", "Mechatronics", "0957-4158"),
    _journal("asme_mechanical_design", "ASME Journal of Mechanical Design", "1050-0472"),
    _journal("computational_mechanics", "Computational Mechanics", "0178-7675"),
    _journal("imeche_k", "Proceedings of the IMechE Part K: J. Multi-body Dynamics", "1464-4193"),
    _journal("mechanical_system_dynamics", "International Journal of Mechanical System Dynamics", "2767-1402"),
    _journal("biomimetics", "Biomimetics", "2313-7673"),
    _journal("bioinspiration_biomimetics", "Bioinspiration & Biomimetics", "1748-3182"),
    _journal("bionic_engineering", "Journal of Bionic Engineering", "1672-6529"),
    _journal("cyborg_bionic_systems", "Cyborg and Bionic Systems", "2097-1087"),
    _journal("asme_biomechanical", "ASME Journal of Biomechanical Engineering", "0148-0731"),
    _journal("imeche_h", "Proceedings of the IMechE Part H: J. Engineering in Medicine", "0954-4119"),
    _journal("asme_tribology", "ASME Journal of Tribology", "0742-4787"),
    _journal("imeche_j", "Proceedings of the IMechE Part J: J. Engineering Tribology", "1350-6501"),
    _journal("tribology_international", "Tribology International", "0301-679X"),
    _journal("asme_heat_mass", "ASME Journal of Heat and Mass Transfer", "2832-8450"),
    _journal("asme_fluids", "ASME Journal of Fluids Engineering", "0098-2202"),
    _journal("asme_thermal", "ASME Journal of Thermal Science and Engineering Applications", "1948-5085"),
    _journal("international_heat_mass", "International Journal of Heat and Mass Transfer", "0017-9310"),
    _journal("annual_reviews_heat", "Annual Reviews of Heat Transfer", "1049-0787"),
    _journal("jmems", "IEEE/ASME Journal of Microelectromechanical Systems (JMEMS)", "1057-7157"),
    _journal("sensors", "Sensors (MDPI)", "1424-8220"),
    _journal("sensors_a", "Sensors and Actuators A: Physical", "0924-4247"),
    _journal("sensors_b", "Sensors and Actuators B: Chemical", "0925-4005"),
    _journal("actuators", "Actuators (MDPI)", "2076-0825"),
    _journal("asme_autonomous_vehicles", "ASME Journal of Autonomous Vehicles and Systems", "2690-702X"),
    _journal("ieee_intelligent_vehicles", "IEEE Transactions on Intelligent Vehicles", "2379-8858"),
    _journal("sae_nvh", "SAE International Journal of Vehicle Dynamics, Stability, and NVH", "2380-2162"),
    _journal("sae_connected_vehicles", "SAE International Journal of Connected and Automated Vehicles", "2574-0741"),
    _journal("sae_materials_manufacturing", "SAE International Journal of Materials and Manufacturing", "1946-3979"),
    _journal("sae_engines", "SAE International Journal of Engines", "1946-3936"),
    _journal("sae_passenger_vehicle", "SAE International Journal of Passenger Vehicle Systems", "2770-3460"),
    _journal("sae_electrified_vehicles", "SAE International Journal of Electrified Vehicles", "2691-3747"),
    _journal("sae_sustainable_transport", "SAE International Journal of Sustainable Transportation, Energy, Environment, & Policy", "2640-642X"),
    _journal("sae_mobility", "SAE International Journal of Advances and Current Practices in Mobility", "2641-9645"),
    _journal("sae_aerospace", "SAE International Journal of Aerospace", "1946-3855"),
    _journal("sae_transportation_safety", "SAE International Journal of Transportation Safety", "2327-5626"),
    _journal("sae_cybersecurity", "SAE International Journal of Transportation Cybersecurity and Privacy", "2572-1046"),
    _journal("imeche_d", "Proceedings of the IMechE Part D: J. Automobile Engineering", "0954-4070"),
    _journal("imeche_f", "Proceedings of the IMechE Part F: J. Rail and Rapid Transit", "0954-4097"),
    _journal("imeche_g", "Proceedings of the IMechE Part G: J. Aerospace Engineering", "0954-4100"),
    _journal("vehicle_system_dynamics", "Vehicle System Dynamics", "0042-3114"),
    _journal("aerospace_mdpi", "Aerospace (MDPI)", "2226-4310"),
    _journal("cjme", "Chinese Journal of Mechanical Engineering (CJME)", "1000-9345"),
    _journal("brazilian_mechanical_sciences", "Journal of the Brazilian Society of Mechanical Sciences and Engineering", "1678-5878"),
    _journal("iranian_mechanical_engineering", "Iranian Journal of Science and Technology - Trans. of Mechanical Engineering", "2228-6187"),
    _journal("advances_mechanical_engineering", "Advances in Mechanical Engineering", "1687-8132"),
    _journal("frontiers_mechanical_engineering", "Frontiers in Mechanical Engineering", "2297-3079"),
    _journal("mechanical_sciences", "Mechanical Sciences (Copernicus)", "2191-9151"),
    _journal("imeche_c", "Proceedings of the IMechE Part C: J. Mechanical Engineering Science", "0263-7154"),
    _journal("imeche_a", "Proceedings of the IMechE Part A: J. Power and Energy", "0957-6509"),
    _journal("imeche_e", "Proceedings of the IMechE Part E: J. Process Mechanical Engineering", "0954-4089"),
    _journal("imeche_l", "Proceedings of the IMechE Part L: J. Materials: Design and Applications", "1464-4207"),
    _journal("imeche_m", "Proceedings of the IMechE Part M: J. Engineering for the Maritime Environment", "1475-0902"),
    _journal("imeche_n", "Proceedings of the IMechE Part N: J. Nanomaterials, Nanoengineering and Nanosystems", "2397-7914"),
    _journal("imeche_o", "Proceedings of the IMechE Part O: J. Risk and Reliability", "1748-006X"),
    _journal("imeche_p", "Proceedings of the IMechE Part P: J. Sports Engineering and Technology", "1754-3371"),
    _journal("archives_civil_mechanical", "Archives of Civil and Mechanical Engineering", "1644-9665"),
    _journal("data_centric_engineering", "Data-Centric Engineering", "2632-6736"),
    _journal("plos_one", "PLOS ONE", "1932-6203"),
    _journal("scientific_reports", "Scientific Reports", "2045-2322"),
    _journal("nature_communications", "Nature Communications", "2041-1723"),
    _journal("peerj", "PeerJ", "2167-8359"),
    _journal("heliyon", "Heliyon", "2405-8440"),
    _journal("ieee_access", "IEEE Access", "2169-3536"),
    _journal("science_robotics", "Science Robotics", "2470-9476"),
    _journal("robotics_mdpi", "Robotics", "2218-6581"),
)


_ACTIVE_CONFERENCE_VENUES: Final[tuple[VenueSpec, ...]] = (
    _conference_issn("icra", "ICRA", "1050-4729"),
    _conference_issn("iros", "IROS", "2153-0858"),
    _conference_sources("rss", "RSS", ("S4306420803",)),
    _conference_issn("ieee_case", "IEEE CASE", "2161-8070"),
    _conference_sources("corl", "CoRL", ("S4306506823",)),
    _conference_sources("iser", "ISER", ("S4306420145",)),
    _conference_sources("robio", "ROBIO", ("S4363607846", "S4363607789")),
    _conference_sources("icar", "ICAR", ("S4306419104",)),
    _conference_issn("aim", "AIM", "2159-6247"),
    _conference_issn("ro_man", "RO-MAN", "1944-9437"),
    _conference_issn("biorob", "BioRob", "2155-1774"),
    _conference_issn("icorr", "ICORR", "1945-7898"),
    _conference_sources("world_haptics", "World Haptics", ("S4306421330",)),
    _conference_issn("ieee_haptics_symposium", "IEEE Haptics Symposium", "2324-7347"),
    _conference_sources("ssrr", "SSRR", ("S4306420208",)),
    _conference_sources("icuas", "ICUAS", ("S4306419904",)),
    _conference_sources("arso", "ARSO", ("S4306417612",)),
    _conference_sources("fsr", "FSR", ("S4306418417",)),
    _conference_sources("dars", "DARS", ("S4306418211",)),
    _conference_sources("icarcv", "ICARCV", ("S4306419301",)),
    _conference_issn("isarc", "ISARC", "2413-5844"),
    _conference_sources("living_machines", "Living Machines", ("S4306418026",)),
)

_UNAVAILABLE_OPENALEX_CONFERENCES: Final[tuple[UnavailableVenue, ...]] = (
    UnavailableVenue(
        key="humanoids",
        display_name="Humanoids",
        source_type="conference",
        reason=(
            "Anonymous OpenAlex lookup returns 404 for exact ISSN 2164-0572, "
            "and rate-limited name lookup left no verified source ID."
        ),
    ),
    UnavailableVenue(
        key="hri",
        display_name="ACM/IEEE HRI",
        source_type="conference",
        reason=(
            "Anonymous OpenAlex lookup returns 404 for exact ISSN 2167-2148, "
            "and rate-limited name lookup left no verified source ID."
        ),
    ),
    UnavailableVenue(
        key="robosoft",
        display_name="RoboSoft",
        source_type="conference",
        reason=(
            "Anonymous OpenAlex lookup returns 404 for exact ISSN 2769-4526, "
            "and rate-limited name lookup left no verified source ID."
        ),
    ),
    UnavailableVenue(
        key="isrr",
        display_name="ISRR",
        source_type="conference",
        reason=(
            "OpenAlex exposes only broad Springer robotics book-series sources, "
            "which do not identify ISRR deterministically."
        ),
    ),
    UnavailableVenue(
        key="mrs",
        display_name="MRS",
        source_type="conference",
        reason=(
            "IEEE MRS proceedings have no series ISSN, and verified OpenAlex "
            "works expose no primary source."
        ),
    ),
    UnavailableVenue(
        key="syroco",
        display_name="SYROCO",
        source_type="conference",
        reason=(
            "OpenAlex exposes only the broad IFAC-PapersOnLine source, which "
            "does not identify SYROCO deterministically."
        ),
    ),
)

CONFERENCE_VENUES: Final[
    tuple[VenueSpec | UnavailableVenue, ...]
] = _ACTIVE_CONFERENCE_VENUES + _UNAVAILABLE_OPENALEX_CONFERENCES

UNAVAILABLE_OPENALEX_JOURNALS: Final[tuple[UnavailableVenue, ...]] = ()

ALL_VENUES: Final[tuple[VenueSpec, ...]] = (
    JOURNAL_VENUES + _ACTIVE_CONFERENCE_VENUES
)
