"""Tests for memory.user_profile — UserProfile + _deep_merge."""

from memory.user_profile import UserProfile, _deep_merge


def test_set_and_get_name(profile):
    assert profile.get_name() is None
    profile.set_name("Jan")
    assert profile.get_name() == "Jan"


def test_deep_merge_dict():
    target = {"a": {"x": 1, "y": 2}}
    source = {"a": {"y": 3, "z": 4}}
    _deep_merge(target, source)
    assert target == {"a": {"x": 1, "y": 3, "z": 4}}


def test_deep_merge_list_dedup():
    target = {"items": ["a", "b"]}
    source = {"items": ["b", "c"]}
    _deep_merge(target, source)
    assert target["items"] == ["a", "b", "c"]


def test_deep_merge_scalar_overwrite():
    target = {"name": "old"}
    source = {"name": "new"}
    _deep_merge(target, source)
    assert target["name"] == "new"


def test_deep_merge_skip_none_and_empty():
    target = {"name": "Jan"}
    source = {"name": None}
    _deep_merge(target, source)
    assert target["name"] == "Jan"

    source2 = {"name": ""}
    _deep_merge(target, source2)
    assert target["name"] == "Jan"


def test_deep_merge_new_key():
    target = {"a": 1}
    source = {"b": 2}
    _deep_merge(target, source)
    assert target == {"a": 1, "b": 2}


def test_deep_merge_changelog():
    target = {"name": "Jan", "age": 30}
    source = {"name": "Petr", "age": 31}
    changelog = []
    _deep_merge(target, source, changelog=changelog)

    assert target["name"] == "Petr"
    assert target["age"] == 31
    assert len(changelog) == 2

    fields = {e["field"] for e in changelog}
    assert fields == {"name", "age"}

    name_entry = next(e for e in changelog if e["field"] == "name")
    assert name_entry["old"] == "Jan"
    assert name_entry["new"] == "Petr"
    assert "date" in name_entry


def test_deep_merge_changelog_nested():
    target = {"basic": {"name": "Jan", "age": 30}}
    source = {"basic": {"name": "Petr"}}
    changelog = []
    _deep_merge(target, source, changelog=changelog)

    assert len(changelog) == 1
    assert changelog[0]["field"] == "basic.name"


def test_deep_merge_skips_version_and_changelog():
    target = {"version": 2, "_changelog": [{"old": "entry"}], "name": "Jan"}
    source = {"version": 99, "_changelog": [{"new": "entry"}], "name": "Petr"}
    _deep_merge(target, source)

    assert target["version"] == 2  # not overwritten
    assert target["_changelog"] == [{"old": "entry"}]  # not overwritten
    assert target["name"] == "Petr"  # normal merge


def test_update_from_extraction_structured(profile):
    profile.set_name("Jan")
    data = {
        "life": {"occupation": "programátor"},
        "interests": {"hobbies": ["šachy"]},
    }
    profile.update_from_extraction(data)

    full = profile.get_full_profile()
    assert full["life"]["occupation"] == "programátor"
    assert "šachy" in full["interests"]["hobbies"]


def test_update_from_extraction_legacy(profile):
    # Legacy format: "name" and "facts" don't overlap with structured_keys.
    # Note: "interests"/"preferences" DO overlap and would trigger structured path.
    data = {
        "name": "Jan",
        "facts": {"pet": "kočka"},
    }
    profile.update_from_extraction(data)

    full = profile.get_full_profile()
    assert full["basic"]["name"] == "Jan"
    assert full["context"]["misc_facts"]["pet"] == "kočka"


def test_changelog_trim(profile):
    profile.set_name("Jan")
    full = profile.get_full_profile()
    # Pre-fill with 19 changelog entries
    full["_changelog"] = [{"field": f"f{i}", "old": "a", "new": "b", "date": "2025-01-01"}
                          for i in range(19)]
    profile._profile = full
    profile._save()

    # Trigger 2 more changes (should bring total to 21, then trim to 20)
    data = {"basic": {"age": 25}}
    profile.update_from_extraction(data)

    data2 = {"basic": {"age": 26}}
    profile.update_from_extraction(data2)

    result = profile.get_full_profile()
    assert len(result.get("_changelog", [])) <= 20


def test_update_from_extraction_with_observations(profile):
    profile.set_name("Jan")
    data = {
        "eigy_observations": {
            "behavioral_patterns": ["uživatel má tendenci odbíhat od tématu"],
        },
    }
    profile.update_from_extraction(data)

    full = profile.get_full_profile()
    assert "eigy_observations" in full
    assert "uživatel má tendenci odbíhat od tématu" in full["eigy_observations"]["behavioral_patterns"]


def test_observations_deduplication(profile):
    profile.set_name("Jan")
    data1 = {"eigy_observations": {"behavioral_patterns": ["rád vtipkuje"]}}
    data2 = {"eigy_observations": {"behavioral_patterns": ["rád vtipkuje", "píše krátce"]}}
    profile.update_from_extraction(data1)
    profile.update_from_extraction(data2)

    full = profile.get_full_profile()
    patterns = full["eigy_observations"]["behavioral_patterns"]
    assert patterns.count("rád vtipkuje") == 1
    assert "píše krátce" in patterns


def test_people_extraction_new_person(profile):
    """Test that a new person is added to the people section."""
    data = {
        "people": {
            "Robert": {
                "relation": "strýc",
                "notes": ["zručný", "rád jezdí do lesa"],
                "location": "Hejnice u Žamberka",
            }
        }
    }
    profile.update_from_extraction(data)

    full = profile.get_full_profile()
    assert "Robert" in full["people"]
    assert full["people"]["Robert"]["relation"] == "strýc"
    assert "zručný" in full["people"]["Robert"]["notes"]
    assert full["people"]["Robert"]["location"] == "Hejnice u Žamberka"


def test_people_merge_existing_person(profile):
    """Test that new info about an existing person is merged."""
    data1 = {
        "people": {
            "Robert": {
                "relation": "strýc",
                "notes": ["zručný"],
            }
        }
    }
    data2 = {
        "people": {
            "Robert": {
                "notes": ["zručný", "rád jezdí do lesa"],
                "location": "Hejnice u Žamberka",
            }
        }
    }
    profile.update_from_extraction(data1)
    profile.update_from_extraction(data2)

    full = profile.get_full_profile()
    robert = full["people"]["Robert"]
    assert robert["relation"] == "strýc"
    assert robert["notes"].count("zručný") == 1  # deduplicated
    assert "rád jezdí do lesa" in robert["notes"]
    assert robert["location"] == "Hejnice u Žamberka"


def test_people_multiple_persons(profile):
    """Test that multiple people can be stored."""
    data = {
        "people": {
            "Robert": {"relation": "strýc", "notes": ["zručný"]},
            "Vlaďka": {"relation": "teta", "notes": ["bydlí ve statku"]},
            "Jindřich": {"relation": "kamarád strýce Roberta", "notes": ["přezdívka Pinďa"]},
        }
    }
    profile.update_from_extraction(data)

    full = profile.get_full_profile()
    assert len(full["people"]) == 3
    assert full["people"]["Vlaďka"]["relation"] == "teta"
    assert "přezdívka Pinďa" in full["people"]["Jindřich"]["notes"]
