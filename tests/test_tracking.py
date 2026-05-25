from knet_reconciler.tracking import (
    DHL,
    FEDEX,
    UNKNOWN,
    UPS,
    USPS,
    detect_carrier_from_number,
    extract_tracking,
    normalize,
)


class TestNormalize:
    def test_strips_spaces_and_dashes_and_uppercases(self):
        assert normalize("1Z 999 AA1 012345 6784") == "1Z999AA10123456784"
        assert normalize("jjd-000-123-456-789") == "JJD000123456789"
        assert normalize("") == ""


class TestDetectCarrier:
    def test_ups(self):
        assert detect_carrier_from_number("1Z999AA10123456784") == UPS
        assert detect_carrier_from_number("1z999aa1 0123 456784") == UPS

    def test_usps_imp_b(self):
        # 20-digit IMpb starting with 94 → USPS
        assert detect_carrier_from_number("94001234567890123456") == USPS
        # 26-digit USPS
        assert detect_carrier_from_number("9400" + "1" * 22) == USPS

    def test_usps_international(self):
        assert detect_carrier_from_number("LZ123456789US") == USPS

    def test_fedex_12_and_15(self):
        assert detect_carrier_from_number("123456789012") == FEDEX
        assert detect_carrier_from_number("123456789012345") == FEDEX

    def test_dhl_jd_prefix(self):
        assert detect_carrier_from_number("JJD000123456789") == DHL
        assert detect_carrier_from_number("JD012345678901") == DHL

    def test_dhl_short_numeric(self):
        assert detect_carrier_from_number("1234567890") == DHL
        assert detect_carrier_from_number("12345678901") == DHL

    def test_unknown(self):
        assert detect_carrier_from_number("not a tracking number") == UNKNOWN
        assert detect_carrier_from_number("12345") == UNKNOWN


class TestExtractFromURL:
    def test_fedex_trknbr(self):
        text = 'Track it: https://www.fedex.com/fedextrack/?trknbr=123456789012'
        results = extract_tracking(text)
        assert (FEDEX, "123456789012") in [(r.carrier, r.number) for r in results]

    def test_fedex_multi_label(self):
        text = "https://www.fedex.com/apps/fedextrack/?tracknumbers=111111111111,222222222222"
        nums = [r.number for r in extract_tracking(text)]
        assert "111111111111" in nums
        assert "222222222222" in nums

    def test_ups_url(self):
        text = "Track: https://www.ups.com/track?tracknum=1Z999AA10123456784"
        results = extract_tracking(text)
        carriers = [(r.carrier, r.number) for r in results]
        assert (UPS, "1Z999AA10123456784") in carriers

    def test_usps_url(self):
        text = "https://tools.usps.com/go/TrackConfirmAction?tLabels=94001234567890123456"
        results = extract_tracking(text)
        assert any(r.carrier == USPS and r.number == "94001234567890123456" for r in results)

    def test_dhl_url(self):
        text = "https://www.dhl.com/en/express/tracking.html?tracking-id=JJD000123456789&brand=DHL"
        results = extract_tracking(text)
        assert any(r.carrier == DHL and r.number == "JJD000123456789" for r in results)


class TestExtractFromText:
    def test_plain_ups_in_text(self):
        text = "Your tracking number is 1Z999AA10123456784. Thanks."
        results = extract_tracking(text)
        assert (UPS, "1Z999AA10123456784") in [(r.carrier, r.number) for r in results]

    def test_dedupes_url_and_text(self):
        text = (
            "Your number: 1Z999AA10123456784 "
            "https://www.ups.com/track?tracknum=1Z999AA10123456784"
        )
        results = extract_tracking(text)
        assert len([r for r in results if r.number == "1Z999AA10123456784"]) == 1

    def test_returns_empty_for_no_match(self):
        assert extract_tracking("nothing in here, sorry") == []
        assert extract_tracking("") == []

    def test_multiple_carriers_in_one_body(self):
        text = (
            "UPS: 1Z999AA10123456784\n"
            "FedEx: https://fedex.com/fedextrack/?trknbr=987654321098\n"
            "DHL: JJD000999888777\n"
        )
        nums = {r.number for r in extract_tracking(text)}
        assert {"1Z999AA10123456784", "987654321098", "JJD000999888777"} <= nums
