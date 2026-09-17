"""TED eForms/UBL XML parser.

Extracts notices, lots, tenders, organizations, and relationships from real
eForms tags discovered in the dataset. Does not invent tags.
"""

from __future__ import annotations

import logging
import tarfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterator, Optional
import xml.etree.ElementTree as ET

import pandas as pd

logger = logging.getLogger(__name__)

NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "efac": "http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1",
    "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
    "efext": "http://data.europa.eu/p27/eforms-ubl-extensions/1",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}


def local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def el_text(el: Optional[ET.Element]) -> Optional[str]:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t if t else None


def find_text(parent: Optional[ET.Element], path: str) -> Optional[str]:
    if parent is None:
        return None
    return el_text(parent.find(path, NS))


def find_amount(parent: Optional[ET.Element], path: str) -> tuple[Optional[float], Optional[str]]:
    if parent is None:
        return None, None
    el = parent.find(path, NS)
    if el is None:
        return None, None
    raw = el_text(el)
    currency = el.get("currencyID")
    try:
        return (float(raw.replace(",", "")) if raw else None), currency
    except ValueError:
        return None, currency


@dataclass
class ParseResult:
    notices: list[dict] = field(default_factory=list)
    lots: list[dict] = field(default_factory=list)
    tenders: list[dict] = field(default_factory=list)
    organizations: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    procurement_rows: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def extend(self, other: "ParseResult") -> None:
        self.notices.extend(other.notices)
        self.lots.extend(other.lots)
        self.tenders.extend(other.tenders)
        self.organizations.extend(other.organizations)
        self.relationships.extend(other.relationships)
        self.procurement_rows.extend(other.procurement_rows)
        self.errors.extend(other.errors)


class EformsParser:
    """Parse one or many TED eForms XML notices into relational tables."""

    SUPPORTED_ROOTS = {
        "ContractAwardNotice",
        "ContractNotice",
        "PriorInformationNotice",
    }

    def parse_file(self, path: Path | str) -> ParseResult:
        path = Path(path)
        result = ParseResult()
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            result = self.parse_element(root, source_file=str(path.name))
        except ET.ParseError as exc:
            result.errors.append(
                {"source_file": str(path), "error": f"XML ParseError: {exc}"}
            )
        except Exception as exc:  # noqa: BLE001 - batch resilience
            result.errors.append({"source_file": str(path), "error": str(exc)})
        return result

    def parse_element(self, root: ET.Element, source_file: str = "") -> ParseResult:
        result = ParseResult()
        notice_type = local_tag(root.tag)
        if notice_type not in self.SUPPORTED_ROOTS:
            result.errors.append(
                {
                    "source_file": source_file,
                    "error": f"Unsupported root element: {notice_type}",
                }
            )
            return result

        notice_id = find_text(root, "cbc:ID")
        publication_id = find_text(root, ".//efac:Publication/efbc:NoticePublicationID")
        procedure_id = find_text(root, "cbc:ContractFolderID")
        issue_date = find_text(root, "cbc:IssueDate")
        publication_date = find_text(root, ".//efac:Publication/efbc:PublicationDate")
        notice_type_code = find_text(root, "cbc:NoticeTypeCode")
        subtype = find_text(root, ".//efac:NoticeSubType/cbc:SubTypeCode")
        regulatory_domain = find_text(root, "cbc:RegulatoryDomain")
        language = find_text(root, "cbc:NoticeLanguageCode")

        # Root procurement project
        root_pp = root.find("cac:ProcurementProject", NS)
        root_name = find_text(root_pp, "cbc:Name")
        root_proc_type = find_text(root_pp, "cbc:ProcurementTypeCode")
        root_est, root_cur = find_amount(root_pp, ".//cbc:EstimatedOverallContractAmount")
        if root_est is None:
            root_est, root_cur = find_amount(root_pp, ".//cbc:FrameworkMaximumAmount")
        root_cpv = find_text(
            root_pp, "cac:MainCommodityClassification/cbc:ItemClassificationCode"
        )
        root_country = find_text(
            root_pp, ".//cac:RealizedLocation/cac:Address/cac:Country/cbc:IdentificationCode"
        )
        root_region = find_text(
            root_pp, ".//cac:RealizedLocation/cac:Address/cbc:CountrySubentityCode"
        )

        # Buyer from ContractingParty (party ID only; name resolved via Organizations)
        buyer_party_ids: list[str] = []
        for cp in root.findall("cac:ContractingParty", NS):
            pid = find_text(cp, "cac:Party/cac:PartyIdentification/cbc:ID")
            if pid:
                buyer_party_ids.append(pid)
                result.relationships.append(
                    {
                        "entity_a": pid,
                        "entity_b": notice_id or publication_id,
                        "relationship_type": "buyer_of_notice",
                        "source": source_file,
                        "confidence": 1.0,
                        "notice_id": notice_id,
                        "publication_id": publication_id,
                    }
                )

        orgs = self._parse_organizations(root, notice_id, publication_id, source_file)
        result.organizations.extend(orgs)
        org_by_id = {o["org_id"]: o for o in orgs if o.get("org_id")}

        buyer_id = buyer_party_ids[0] if buyer_party_ids else None
        buyer = org_by_id.get(buyer_id, {}) if buyer_id else {}

        notice_row = {
            "notice_id": notice_id,
            "publication_id": publication_id,
            "procedure_id": procedure_id,
            "notice_root": notice_type,
            "notice_type": notice_type_code,
            "notice_subtype": subtype,
            "issue_date": issue_date,
            "publication_date": publication_date,
            "regulatory_domain": regulatory_domain,
            "language": language,
            "title": root_name,
            "procurement_type": root_proc_type,
            "estimated_amount": root_est,
            "currency": root_cur,
            "cpv_code": root_cpv,
            "performance_country": root_country,
            "performance_region": root_region,
            "buyer_id": buyer_id,
            "buyer_name": buyer.get("name"),
            "buyer_country": buyer.get("country"),
            "buyer_region": buyer.get("region"),
            "source_file": source_file,
        }
        result.notices.append(notice_row)

        # Tendering parties (full definitions under NoticeResult / extensions)
        tendering_parties = self._parse_tendering_parties(root)
        for tpa_id, tp in tendering_parties.items():
            for org_id in tp["tenderer_org_ids"]:
                result.relationships.append(
                    {
                        "entity_a": org_id,
                        "entity_b": tpa_id,
                        "relationship_type": "member_of_tendering_party",
                        "source": source_file,
                        "confidence": 1.0,
                        "notice_id": notice_id,
                        "publication_id": publication_id,
                    }
                )

        # Lot results / submission stats (award notices)
        lot_stats = self._parse_lot_results(root)

        # Settled contracts → award dates / winning tender refs
        contracts = self._parse_settled_contracts(root)

        # Lots
        lots = root.findall("cac:ProcurementProjectLot", NS)
        if not lots and root_pp is not None:
            # Single-lot notices may only have root ProcurementProject
            lot_row = self._lot_from_project(
                lot_id="LOT-ROOT",
                pp=root_pp,
                notice_id=notice_id,
                publication_id=publication_id,
                procedure_id=procedure_id,
                source_file=source_file,
                stats=lot_stats.get("LOT-ROOT") or lot_stats.get("RES-0001"),
            )
            result.lots.append(lot_row)
        else:
            for lot in lots:
                lot_id = find_text(lot, "cbc:ID")
                pp = lot.find("cac:ProcurementProject", NS)
                stats = None
                # Match stats by lot via LotTender→TenderLot or RES id heuristics later
                lot_row = self._lot_from_project(
                    lot_id=lot_id,
                    pp=pp,
                    notice_id=notice_id,
                    publication_id=publication_id,
                    procedure_id=procedure_id,
                    source_file=source_file,
                    stats=None,
                )
                result.lots.append(lot_row)

        # Full LotTender records (bids/awards)
        lot_tenders = self._parse_lot_tenders(root)
        # Attach submission stats to lots via tender→lot mapping
        lot_id_to_stats: dict[str, dict] = {}
        for lt in lot_tenders:
            lot_ref = lt.get("lot_id")
            # Find matching LotResult that references this tender
            for res_id, st in lot_stats.items():
                if lt["tender_id"] in st.get("tender_ids", []):
                    lot_id_to_stats[lot_ref] = st
                    break

        for lot in result.lots:
            st = lot_id_to_stats.get(lot.get("lot_id"))
            if st:
                lot["num_submissions"] = st.get("num_esubmissions")
                lot["num_sme_submissions"] = st.get("num_sme")
                lot["tender_result_code"] = st.get("tender_result_code")
                lot["framework_max_amount"] = st.get("framework_max_amount")
                lot["framework_max_currency"] = st.get("framework_max_currency")

        for lt in lot_tenders:
            tpa = tendering_parties.get(lt.get("tendering_party_id") or "", {})
            vendor_ids = tpa.get("tenderer_org_ids") or []
            vendor_id = vendor_ids[0] if vendor_ids else None
            vendor = org_by_id.get(vendor_id, {}) if vendor_id else {}
            vendor_name = tpa.get("name") or vendor.get("name")

            contract = contracts.get(lt["tender_id"], {})
            lot_meta = next(
                (x for x in result.lots if x.get("lot_id") == lt.get("lot_id")),
                {},
            )
            st = lot_id_to_stats.get(lt.get("lot_id"), {})

            tender_row = {
                "notice_id": notice_id,
                "publication_id": publication_id,
                "procedure_id": procedure_id,
                "tender_id": lt["tender_id"],
                "lot_id": lt.get("lot_id"),
                "tender_reference": lt.get("tender_reference"),
                "tendering_party_id": lt.get("tendering_party_id"),
                "tender_rank": lt.get("tender_rank"),
                "tender_ranked": lt.get("tender_ranked"),
                "bid_amount": lt.get("payable_amount"),
                "currency": lt.get("currency") or lot_meta.get("currency") or root_cur,
                "subcontracting": lt.get("subcontracting"),
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "vendor_org_ids": "|".join(vendor_ids) if vendor_ids else None,
                "award_date": contract.get("award_date"),
                "contract_id": contract.get("contract_id"),
                "contract_title": contract.get("contract_title"),
                "is_winner": True if notice_type == "ContractAwardNotice" and lt.get("tender_rank") == 1 else (
                    True if notice_type == "ContractAwardNotice" and lt.get("tender_rank") is None else None
                ),
                "num_submissions": st.get("num_esubmissions") or lot_meta.get("num_submissions"),
                "source_file": source_file,
            }
            # Award notices typically list winning LotTenders; treat presence as awarded
            if notice_type == "ContractAwardNotice":
                tender_row["is_winner"] = True
                tender_row["tender_status"] = "awarded"
            else:
                tender_row["tender_status"] = "submitted"
            result.tenders.append(tender_row)

            if vendor_id:
                result.relationships.append(
                    {
                        "entity_a": vendor_id,
                        "entity_b": lt["tender_id"],
                        "relationship_type": "won" if tender_row["is_winner"] else "participated_in",
                        "source": source_file,
                        "confidence": 1.0,
                        "notice_id": notice_id,
                        "publication_id": publication_id,
                    }
                )
                if buyer_id:
                    result.relationships.append(
                        {
                            "entity_a": buyer_id,
                            "entity_b": vendor_id,
                            "relationship_type": "buyer_awarded_vendor",
                            "source": source_file,
                            "confidence": 1.0,
                            "notice_id": notice_id,
                            "publication_id": publication_id,
                        }
                    )

            # Analytical row (one per tender/award; for CN without tenders, add lot-level later)
            result.procurement_rows.append(
                {
                    "notice_id": notice_id,
                    "publication_id": publication_id,
                    "procedure_id": procedure_id,
                    "notice_type": notice_type_code,
                    "notice_root": notice_type,
                    "publication_date": publication_date,
                    "award_date": tender_row.get("award_date"),
                    "lot_id": lt.get("lot_id"),
                    "lot_reference": lt.get("tender_reference"),
                    "lot_name": lot_meta.get("lot_name"),
                    "buyer_id": buyer_id,
                    "buyer_name": buyer.get("name"),
                    "buyer_country": buyer.get("country"),
                    "buyer_region": buyer.get("region"),
                    "vendor_id": vendor_id,
                    "vendor_name": vendor_name,
                    "tender_id": lt["tender_id"],
                    "tender_rank": lt.get("tender_rank"),
                    "bid_amount": lt.get("payable_amount"),
                    "estimated_amount": lot_meta.get("estimated_amount") or root_est,
                    "currency": tender_row.get("currency"),
                    "num_submissions": tender_row.get("num_submissions"),
                    "cpv_code": lot_meta.get("cpv_code") or root_cpv,
                    "performance_country": lot_meta.get("performance_country") or root_country,
                    "performance_region": lot_meta.get("performance_region") or root_region,
                    "subcontracting": lt.get("subcontracting"),
                    "tender_status": tender_row.get("tender_status"),
                    "procurement_type": lot_meta.get("procurement_type") or root_proc_type,
                    "title": root_name,
                    "source_file": source_file,
                }
            )

        # ContractNotice / PIN without LotTenders → still emit procurement rows per lot
        if not lot_tenders:
            for lot in result.lots:
                result.procurement_rows.append(
                    {
                        "notice_id": notice_id,
                        "publication_id": publication_id,
                        "procedure_id": procedure_id,
                        "notice_type": notice_type_code,
                        "notice_root": notice_type,
                        "publication_date": publication_date,
                        "award_date": None,
                        "lot_id": lot.get("lot_id"),
                        "lot_reference": None,
                        "lot_name": lot.get("lot_name"),
                        "buyer_id": buyer_id,
                        "buyer_name": buyer.get("name"),
                        "buyer_country": buyer.get("country"),
                        "buyer_region": buyer.get("region"),
                        "vendor_id": None,
                        "vendor_name": None,
                        "tender_id": None,
                        "tender_rank": None,
                        "bid_amount": None,
                        "estimated_amount": lot.get("estimated_amount") or root_est,
                        "currency": lot.get("currency") or root_cur,
                        "num_submissions": lot.get("num_submissions"),
                        "cpv_code": lot.get("cpv_code") or root_cpv,
                        "performance_country": lot.get("performance_country") or root_country,
                        "performance_region": lot.get("performance_region") or root_region,
                        "subcontracting": None,
                        "tender_status": None,
                        "procurement_type": lot.get("procurement_type") or root_proc_type,
                        "title": root_name,
                        "source_file": source_file,
                    }
                )

        # Co-bidding: all vendors linked via tendering parties on same notice
        # (award notices often only list winners; co-bid edges built later if multi-bidder rows exist)
        vendor_ids_on_notice = [
            o["org_id"]
            for o in orgs
            if o.get("org_id") and o.get("org_id") not in buyer_party_ids
            and o.get("org_id") not in self._likely_authority_ids(orgs, buyer_party_ids)
        ]
        # Prefer tendering-party vendors
        tp_vendors = []
        for tp in tendering_parties.values():
            tp_vendors.extend(tp.get("tenderer_org_ids") or [])
        tp_vendors = sorted(set(tp_vendors))
        if len(tp_vendors) >= 2:
            for i in range(len(tp_vendors)):
                for j in range(i + 1, len(tp_vendors)):
                    result.relationships.append(
                        {
                            "entity_a": tp_vendors[i],
                            "entity_b": tp_vendors[j],
                            "relationship_type": "co_listed_on_notice",
                            "source": source_file,
                            "confidence": 0.6,
                            "notice_id": notice_id,
                            "publication_id": publication_id,
                        }
                    )

        return result

    def _likely_authority_ids(
        self, orgs: list[dict], buyer_ids: list[str]
    ) -> set[str]:
        """Heuristic: review bodies / regulators often appear as ORGs but are not vendors."""
        skip = set(buyer_ids)
        keywords = ("autorit", "tribun", "tar ", "court", "commission", "ministry")
        for o in orgs:
            name = (o.get("name") or "").lower()
            if any(k in name for k in keywords):
                skip.add(o["org_id"])
        return skip

    def _lot_from_project(
        self,
        lot_id: Optional[str],
        pp: Optional[ET.Element],
        notice_id: Optional[str],
        publication_id: Optional[str],
        procedure_id: Optional[str],
        source_file: str,
        stats: Optional[dict],
    ) -> dict:
        est, cur = find_amount(pp, ".//cbc:EstimatedOverallContractAmount")
        if est is None:
            est, cur = find_amount(pp, ".//cbc:FrameworkMaximumAmount")
        row = {
            "notice_id": notice_id,
            "publication_id": publication_id,
            "procedure_id": procedure_id,
            "lot_id": lot_id,
            "lot_name": find_text(pp, "cbc:Name"),
            "procurement_type": find_text(pp, "cbc:ProcurementTypeCode"),
            "estimated_amount": est,
            "currency": cur,
            "cpv_code": find_text(
                pp, "cac:MainCommodityClassification/cbc:ItemClassificationCode"
            ),
            "performance_country": find_text(
                pp,
                ".//cac:RealizedLocation/cac:Address/cac:Country/cbc:IdentificationCode",
            ),
            "performance_region": find_text(
                pp, ".//cac:RealizedLocation/cac:Address/cbc:CountrySubentityCode"
            ),
            "num_submissions": (stats or {}).get("num_esubmissions"),
            "num_sme_submissions": (stats or {}).get("num_sme"),
            "tender_result_code": (stats or {}).get("tender_result_code"),
            "framework_max_amount": (stats or {}).get("framework_max_amount"),
            "framework_max_currency": (stats or {}).get("framework_max_currency"),
            "source_file": source_file,
        }
        return row

    def _parse_organizations(
        self,
        root: ET.Element,
        notice_id: Optional[str],
        publication_id: Optional[str],
        source_file: str,
    ) -> list[dict]:
        rows = []
        for org in root.findall(".//efac:Organizations/efac:Organization", NS):
            company = org.find("efac:Company", NS)
            if company is None:
                continue
            org_id = find_text(company, "cac:PartyIdentification/cbc:ID")
            rows.append(
                {
                    "org_id": org_id,
                    "name": find_text(company, "cac:PartyName/cbc:Name"),
                    "company_id": find_text(company, "cac:PartyLegalEntity/cbc:CompanyID"),
                    "country": find_text(
                        company, "cac:PostalAddress/cac:Country/cbc:IdentificationCode"
                    ),
                    "region": find_text(
                        company, "cac:PostalAddress/cbc:CountrySubentityCode"
                    ),
                    "city": find_text(company, "cac:PostalAddress/cbc:CityName"),
                    "postal_zone": find_text(company, "cac:PostalAddress/cbc:PostalZone"),
                    "street": find_text(company, "cac:PostalAddress/cbc:StreetName"),
                    "website": find_text(company, "cbc:WebsiteURI"),
                    # Contact fields extracted but flagged for privacy handling downstream
                    "telephone": find_text(company, "cac:Contact/cbc:Telephone"),
                    "email": find_text(company, "cac:Contact/cbc:ElectronicMail"),
                    "notice_id": notice_id,
                    "publication_id": publication_id,
                    "source_file": source_file,
                }
            )
        return rows

    def _parse_tendering_parties(self, root: ET.Element) -> dict[str, dict]:
        parties: dict[str, dict] = {}
        for tp in root.findall(".//efac:TenderingParty", NS):
            tpa_id = find_text(tp, "cbc:ID")
            if not tpa_id:
                continue
            name = find_text(tp, "cbc:Name")
            tenderer_ids = [
                find_text(t, "cbc:ID")
                for t in tp.findall("efac:Tenderer", NS)
                if find_text(t, "cbc:ID")
            ]
            # Keep the richest definition (some refs are ID-only stubs)
            existing = parties.get(tpa_id)
            if existing is None or (name and not existing.get("name")) or (
                tenderer_ids and not existing.get("tenderer_org_ids")
            ):
                parties[tpa_id] = {
                    "tpa_id": tpa_id,
                    "name": name or (existing or {}).get("name"),
                    "tenderer_org_ids": tenderer_ids
                    or (existing or {}).get("tenderer_org_ids")
                    or [],
                }
        return parties

    def _parse_lot_results(self, root: ET.Element) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for lr in root.findall(".//efac:LotResult", NS):
            res_id = find_text(lr, "cbc:ID") or f"RES-{len(out)+1}"
            stats: dict[str, Any] = {
                "tender_result_code": find_text(lr, "cbc:TenderResultCode"),
                "tender_ids": [
                    find_text(lt, "cbc:ID")
                    for lt in lr.findall("efac:LotTender", NS)
                    if find_text(lt, "cbc:ID")
                ],
            }
            for s in lr.findall("efac:ReceivedSubmissionsStatistics", NS):
                code = find_text(s, "efbc:StatisticsCode")
                num = find_text(s, "efbc:StatisticsNumeric")
                try:
                    num_v = int(float(num)) if num is not None else None
                except ValueError:
                    num_v = None
                if code == "t-esubm":
                    stats["num_esubmissions"] = num_v
                elif code == "t-sme":
                    stats["num_sme"] = num_v
            fa = lr.find("efac:FrameworkAgreementValues", NS)
            if fa is not None:
                amt, cur = find_amount(fa, "cbc:MaximumValueAmount")
                stats["framework_max_amount"] = amt
                stats["framework_max_currency"] = cur
            out[res_id] = stats
        return out

    def _parse_settled_contracts(self, root: ET.Element) -> dict[str, dict]:
        """Map tender_id -> contract metadata (prefer full SettledContract nodes)."""
        out: dict[str, dict] = {}
        for sc in root.findall(".//efac:SettledContract", NS):
            kids = [local_tag(c.tag) for c in list(sc)]
            if "AwardDate" not in kids and "IssueDate" not in kids:
                continue
            contract_id = find_text(sc, "cbc:ID")
            award_date = find_text(sc, "cbc:AwardDate")
            issue_date = find_text(sc, "cbc:IssueDate")
            title = find_text(sc, "efac:ContractReference/cbc:ID")
            for lt in sc.findall("efac:LotTender", NS):
                tid = find_text(lt, "cbc:ID")
                if tid:
                    out[tid] = {
                        "contract_id": contract_id,
                        "award_date": award_date,
                        "contract_issue_date": issue_date,
                        "contract_title": title,
                    }
        return out

    def _parse_lot_tenders(self, root: ET.Element) -> list[dict]:
        rows = []
        seen = set()
        for lt in root.findall(".//efac:LotTender", NS):
            kids = [local_tag(c.tag) for c in list(lt)]
            # Skip reference-only stubs (ID alone)
            if not any(
                k in kids
                for k in (
                    "RankCode",
                    "LegalMonetaryTotal",
                    "TenderingParty",
                    "TenderLot",
                    "SubcontractingTerm",
                )
            ):
                continue
            tender_id = find_text(lt, "cbc:ID")
            if not tender_id or tender_id in seen:
                continue
            # Prefer nodes with monetary totals when duplicates exist
            payable, currency = find_amount(lt, "cac:LegalMonetaryTotal/cbc:PayableAmount")
            rank_raw = find_text(lt, "cbc:RankCode")
            try:
                rank = int(float(rank_raw)) if rank_raw is not None else None
            except ValueError:
                rank = None
            rows.append(
                {
                    "tender_id": tender_id,
                    "tender_rank": rank,
                    "tender_ranked": find_text(lt, "cbc:TenderRankedIndicator"),
                    "payable_amount": payable,
                    "currency": currency,
                    "subcontracting": find_text(
                        lt, "cac:SubcontractingTerm/cbc:TermCode"
                    ),
                    "tendering_party_id": find_text(lt, "efac:TenderingParty/cbc:ID"),
                    "lot_id": find_text(lt, "efac:TenderLot/cbc:ID"),
                    "tender_reference": find_text(lt, "efac:TenderReference/cbc:ID"),
                }
            )
            seen.add(tender_id)
        return rows


def iter_xml_paths(source_dir: Path, extract_archives: bool = True) -> Iterator[Path]:
    """Yield XML paths from extracted folders and optionally from .tar.gz archives."""
    source_dir = Path(source_dir)
    yielded = set()

    # Already-extracted directories
    for xml_path in sorted(source_dir.rglob("*.xml")):
        # Prefer extracted copies; skip if inside a weird temp
        key = xml_path.name
        if key not in yielded:
            yielded.add(key)
            yield xml_path

    if not extract_archives:
        return

    # Also parse XML directly from tar.gz without requiring full extraction
    for archive in sorted(source_dir.glob("*.tar.gz")):
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = [m for m in tar.getmembers() if m.name.endswith(".xml")]
                # If corresponding extracted folder exists with same stem-ish, skip archive
                # to avoid duplicate processing of already-yielded names
                for member in members:
                    name = Path(member.name).name
                    if name in yielded:
                        continue
                    # Extract to a temp-like marker: yield via special handling in batch
                    # We cannot yield a Path inside tar; handled by process_source
                    yielded.add(name)
                    yield Path(f"tar://{archive}::{member.name}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot open archive %s: %s", archive, exc)


def read_xml_bytes(path: Path) -> bytes:
    """Read XML from filesystem or tar://archive::member pseudo-path."""
    s = str(path)
    if s.startswith("tar://"):
        rest = s[6:]
        archive, member = rest.split("::", 1)
        with tarfile.open(archive, "r:gz") as tar:
            f = tar.extractfile(member)
            if f is None:
                raise FileNotFoundError(member)
            return f.read()
    return Path(path).read_bytes()


def parse_path(parser: EformsParser, path: Path) -> ParseResult:
    s = str(path)
    if s.startswith("tar://"):
        result = ParseResult()
        try:
            data = read_xml_bytes(path)
            root = ET.fromstring(data)
            member = s.split("::", 1)[1]
            result = parser.parse_element(root, source_file=Path(member).name)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"source_file": s, "error": str(exc)})
        return result
    return parser.parse_file(path)


def results_to_dataframes(result: ParseResult) -> dict[str, pd.DataFrame]:
    return {
        "notices": pd.DataFrame(result.notices),
        "lots": pd.DataFrame(result.lots),
        "tenders": pd.DataFrame(result.tenders),
        "organizations": pd.DataFrame(result.organizations),
        "relationships": pd.DataFrame(result.relationships),
        "procurement_data": pd.DataFrame(result.procurement_rows),
    }
