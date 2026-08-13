import pytest
from app.classifier import DeterministicClassifier
from app.models import RawPost

classifier = DeterministicClassifier()

def make_post(text: str) -> RawPost:
    return RawPost(
        post_url="http://test",
        post_date="2026-08-11",
        text=text,
        author_name="Tester",
        author_profile_url="http://profile"
    )

def test_genuine_cos_hiring():
    post = make_post("We're hiring a Chief of Staff to join our team!")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.confidence == 1.0

def test_founders_office_hiring():
    post = make_post("Looking for a superstar to join the Founder's office. DM me.")
    result = classifier.classify(post)
    assert result.is_valid == True

def test_generalist_hiring():
    post = make_post("We are looking for a business generalist who can wear many hats.")
    result = classifier.classify(post)
    assert result.is_valid == True

def test_cos_internship():
    post = make_post("We are hiring a chief of staff intern for the summer.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.employment_type == "Internship"

def test_open_to_work_exclusion():
    post = make_post("I'm looking for a Chief of Staff role. Please hire me!")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "exclusion" in result.classification_reason.lower()

def test_congratulatory_exclusion():
    post = make_post("Excited to join Acme Corp as their new Chief of Staff!")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "exclusion" in result.classification_reason.lower()

def test_informational_exclusion():
    # Role keyword is there, but no hiring signal
    post = make_post("A Chief of Staff is a critical role for any growing startup.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "no hiring intent" in result.classification_reason.lower()

def test_proximity_failure():
    # Hiring signal and role keyword are > 300 chars apart
    filler = "x" * 400
    post = make_post(f"We are hiring {filler} and also talking about chief of staff.")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "too far apart" in result.classification_reason.lower()

def test_ambiguous_confidence():
    # 101-300 chars apart should yield 0.8
    filler = "x" * 150
    post = make_post(f"we are hiring {filler} chief of staff")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.confidence == 0.8

def test_product_manager_hiring():
    post = make_post("We're hiring a product manager to own our mobile app roadmap!")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.role_category == "Product Manager"

def test_product_internship():
    post = make_post("Looking for a product management intern for the summer.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.role_category == "Product Manager"
    assert result.employment_type == "Internship"

def test_game_developer_hiring():
    post = make_post("We are looking for a unity developer to build our metaverse game.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.role_category == "Game Developer"

def test_apm_program_hiring():
    post = make_post("Applications open for our associate product manager program, DM to apply.")
    result = classifier.classify(post)
    assert result.is_valid == True
    assert result.role_category == "Product Manager"

def test_game_dev_proximity_failure():
    filler = "x" * 400
    post = make_post(f"we are hiring {filler} and also need a game developer")
    result = classifier.classify(post)
    assert result.is_valid == False
    assert "too far apart" in result.classification_reason.lower()
