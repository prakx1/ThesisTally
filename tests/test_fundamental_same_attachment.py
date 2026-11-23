from thesistally.fundamental_same_attachment import archive_key, before


def test_matching_requires_exact_official_attached_file_including_its_version():
    earlier = "https://nsearchives.nseindia.in/corporate/issuer.pdf?v=2"
    assert archive_key(earlier) == archive_key(earlier.replace("nseindia.in", "nseindia.com"))
    assert archive_key(earlier) != archive_key(earlier.replace("v=2", "v=1"))
    assert archive_key(earlier) != archive_key(earlier.replace("issuer.pdf", "ISSUER.pdf"))
    assert archive_key(earlier.replace("nsearchives.nseindia.in", "example.com")) is None
    assert before("2021-07-26T15:28:18+05:30", "2021-07-26T15:30:00+05:30")
    assert not before("2021-07-26T15:30:00+05:30", "2021-07-26T15:30:00+05:30")
    assert not before("2021-07-10T15:28:18+05:30", "2021-07-26T15:30:00+05:30")
