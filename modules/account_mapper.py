# Module for handling account mapping logic
import logging
from datetime import datetime, timedelta, timezone
import os


def is_simple_value(value):
    """Check if the value is a trivial type: int, float, str, bool, or None"""
    return isinstance(value, (int, float, str, bool)) or value is None


def shallow_compare_dicts(dict1, dict2):
    """Compare dictionaries using only simple values"""
    dict1_filtered = {k: v for k, v in dict1.items() if is_simple_value(v)}
    dict2_filtered = {k: v for k, v in dict2.items() if is_simple_value(v)}
    return dict1_filtered == dict2_filtered


def combine_accounts(latest_accounts, existing_accounts):
    """Combines latest and existing accounts, preserving date_first_loaded."""
    current_date = datetime.now().isoformat()
    combined_accounts = {}
    deleted_accounts = []

    # Convert accounts to dictionaries if they're lists
    if isinstance(latest_accounts, list):
        latest_accounts = {acc["id"]: acc for acc in latest_accounts}
    if isinstance(existing_accounts, list):
        existing_accounts = {acc["id"]: acc for acc in existing_accounts}

    # Merge accounts
    for account_id, account_data in latest_accounts.items():
        if account_id in existing_accounts:
            account_data["date_first_loaded"] = existing_accounts[account_id].get(
                "date_first_loaded", current_date
            )
        else:
            account_data["date_first_loaded"] = current_date
        combined_accounts[account_id] = account_data

    # Identify deleted accounts
    for account_id in existing_accounts:
        if account_id not in latest_accounts:
            deleted_accounts.append(account_id)

    return combined_accounts, deleted_accounts


def merge_and_update_mapping(
    existing_mapping,
    latest_akahu_accounts,
    latest_actual_accounts,
    latest_ynab_accounts,
    existing_akahu_accounts,
    existing_actual_accounts,
    existing_ynab_accounts,
):
    """Merges and updates account mapping"""
    # Combine accounts
    combined_akahu_accounts, deleted_akahu_accounts = combine_accounts(
        latest_akahu_accounts, existing_akahu_accounts
    )
    combined_actual_accounts, deleted_actual_accounts = combine_accounts(
        latest_actual_accounts, existing_actual_accounts
    )
    combined_ynab_accounts, deleted_ynab_accounts = combine_accounts(
        latest_ynab_accounts, existing_ynab_accounts
    )

    # Report deletions
    if deleted_akahu_accounts:
        logging.info(f"{len(deleted_akahu_accounts)} Akahu accounts will be deleted.")
    if deleted_actual_accounts:
        logging.info(f"{len(deleted_actual_accounts)} Actual accounts will be deleted.")
    if deleted_ynab_accounts:
        logging.info(f"{len(deleted_ynab_accounts)} YNAB accounts will be deleted.")

    updated_mapping = existing_mapping.copy()

    # Process deletions if confirmed by user
    if any([deleted_akahu_accounts, deleted_actual_accounts, deleted_ynab_accounts]):
        confirmation = input(
            "Do you want to proceed with deleting these accounts? (Y to confirm):"
        )
        if confirmation.lower() == "y":
            # Process Akahu deletions
            for akahu_id in deleted_akahu_accounts:
                updated_mapping.pop(akahu_id, None)
                logging.info(f"Deleted Akahu account with ID {akahu_id}.")

            # Remove links to deleted Actual/YNAB accounts
            for prefix, deleted_ids in (
                ("actual", deleted_actual_accounts),
                ("ynab", deleted_ynab_accounts),
            ):
                for akahu_id, entry in updated_mapping.items():
                    target_id = entry.get(f"{prefix}_account_id")
                    if target_id not in deleted_ids:
                        continue
                    for suffix in (
                        "account_id",
                        "budget_id",
                        "budget_name",
                        "account_name",
                    ):
                        entry.pop(f"{prefix}_{suffix}", None)
                    logging.info(
                        f"Removed {prefix} account {target_id} from mapping {akahu_id}."
                    )

    return (
        updated_mapping,
        combined_akahu_accounts,
        combined_actual_accounts,
        combined_ynab_accounts,
    )


def validate_user_input(
    response_content, target_accounts, akahu_to_account_mapping, target_account_key
):
    """Validates the user input from OpenAI response to ensure it's a valid selection."""
    try:
        chosen_seq = int(response_content)

        if chosen_seq == 0:
            return 0

        account = next(
            (account for account in target_accounts if account["seq"] == chosen_seq),
            None,
        )
        if account is not None:
            account_id = account["id"]

            if not any(
                account_id == mapping.get(target_account_key)
                for mapping in akahu_to_account_mapping.values()
            ):
                return chosen_seq
    except ValueError:
        return None

    return None


def get_openai_match_suggestion(
    akahu_account, target_accounts, akahu_to_account_mapping, target_account_key
):
    """Get account match suggestion using OpenAI."""
    prompt = (
        "You are an expert in financial account mapping. Your task is to match the given Akahu account with one of the provided target accounts. "
        "Please provide the number corresponding to the best match. Even if you are not completely certain, make the best choice you can based on the information provided.\n\n"
        "Akahu Account:\n"
        f"Name: {akahu_account['name']}\n"
        f"Connection: {akahu_account['connection']}"
    )

    # Check for existing mapping in the other budget system
    if target_account_key == "actual_account_id":
        # Looking for Actual match, check YNAB mapping
        for mapping in akahu_to_account_mapping.values():
            if mapping.get("akahu_name") == akahu_account["name"] and mapping.get(
                "ynab_account_name"
            ):
                prompt += f"\nThis account is already mapped to '{mapping['ynab_account_name']}' in YNAB"
                break
    else:
        # Looking for YNAB match, check Actual mapping
        for mapping in akahu_to_account_mapping.values():
            if mapping.get("akahu_name") == akahu_account["name"] and mapping.get(
                "actual_account_name"
            ):
                prompt += f"\nThis account is already mapped to '{mapping['actual_account_name']}' in Actual Budget"
                break

    prompt += "\n\nHere is a list of target accounts:\n"
    prompt += "0. This account probably does not match any options\n"

    for idx, account in enumerate(target_accounts, start=1):
        account_id = account["id"]
        account_name = account["name"]
        account_seq = account["seq"]

        if not any(
            account_id == mapping.get(target_account_key)
            for mapping in akahu_to_account_mapping.values()
        ):
            prompt += f"{account_seq}. {account_name}\n"

    prompt += "\nPlease type the number corresponding to the best match:"

    try:
        import openai

        client = openai.OpenAI(
            base_url=os.getenv("OPENAI_BASE_URL", None),
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", default="gpt-5.2"),
            messages=[
                {
                    "role": "system",
                    "content": "Select the best match by responding with a number, including 0 if no match is suitable. Respond strictly with a number—no explanations or commentary.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=2,
            temperature=0,
        )

        response_content = response.choices[0].message.content.strip()
        chosen_index = validate_user_input(
            response_content,
            target_accounts,
            akahu_to_account_mapping,
            target_account_key,
        )
        if chosen_index is not None:
            return chosen_index
    except Exception as e:
        logging.warning(
            f"OpenAI match suggestion unavailable ({e}); falling back to fuzzy match."
        )

    return get_fuzzy_match_suggestion(
        akahu_account, target_accounts, akahu_to_account_mapping, target_account_key
    )


def get_fuzzy_match_suggestion(
    akahu_account, target_accounts, akahu_to_account_mapping, target_account_key
):
    """Get account match suggestion using fuzzy matching."""
    # Setup-only dependency; the sync path imports this module without it.
    from fuzzywuzzy import process

    unmapped_accounts = []
    unmapped_indices = []
    seq_to_account = {}  # Map sequence numbers to accounts
    logging.info("Falling back to FuzzyWuzzy for matching suggestion.")

    for target_account in target_accounts:
        account_id = target_account["id"]
        account_name = target_account["name"]
        account_seq = target_account["seq"]
        seq_to_account[account_seq] = target_account

        if not any(
            account_id == mapping.get(target_account_key)
            for mapping in akahu_to_account_mapping.values()
        ):
            unmapped_accounts.append((account_name, account_seq))

    if unmapped_accounts:
        account_names = [acc[0] for acc in unmapped_accounts]
        best_match_name, confidence = process.extractOne(
            akahu_account["name"], account_names
        )
        if confidence >= 50:
            matched_index = account_names.index(best_match_name)
            return unmapped_accounts[matched_index][1]  # Return the sequence number

    return 0  # Return integer 0 instead of string "0"


def seq_to_acct(suggested_index, target_accounts):
    """Convert sequence number to account object."""
    return next(
        (acct for acct in target_accounts if acct["seq"] == suggested_index), None
    )


def match_accounts(
    akahu_to_account_mapping,
    akahu_accounts,
    target_accounts,
    account_type,
    use_openai=True,
):
    """Match Akahu accounts to target accounts (Actual or YNAB)."""
    if account_type == "actual":
        target_account_key = "actual_account_id"
        target_account_name = "actual_account_name"
    elif account_type == "ynab":
        target_account_key = "ynab_account_id"
        target_account_name = "ynab_account_name"
    else:
        raise ValueError(
            "Invalid account type provided. Must be either 'actual' or 'ynab'."
        )

    target_accounts_list = sorted(
        target_accounts.values(), key=lambda x: x["name"].lower()
    )
    for idx, target_account in enumerate(target_accounts_list, start=1):
        target_account["seq"] = idx

    for akahu_id, akahu_account in sorted(
        akahu_accounts.items(), key=lambda x: x[1]["name"].lower()
    ):
        akahu_name = akahu_account["name"]

        if akahu_id in akahu_to_account_mapping:
            if (
                target_account_key in akahu_to_account_mapping[akahu_id]
                or f"{account_type}_do_not_map" in akahu_to_account_mapping[akahu_id]
            ):
                print(
                    f"Akahu account '{akahu_name}' is already mapped or marked as DO NOT MAP for {account_type}. Skipping."
                )
                continue

        suggested_index = (
            get_openai_match_suggestion(
                akahu_account,
                target_accounts_list,
                akahu_to_account_mapping,
                target_account_key,
            )
            if use_openai
            else get_fuzzy_match_suggestion(
                akahu_account,
                target_accounts_list,
                akahu_to_account_mapping,
                target_account_key,
            )
        )

        print(
            f"\nAkahu Account: {akahu_name} (Connection: {akahu_account['connection']}, account number: {akahu_account['formatted_account']})"
        )

        # Show existing mapping from other system
        existing_mapping = akahu_to_account_mapping.get(akahu_id, {})
        if account_type == "actual" and existing_mapping.get("ynab_account_name"):
            print(
                f"Currently mapped in YNAB to: {existing_mapping['ynab_account_name']}"
            )
        elif account_type == "ynab" and existing_mapping.get("actual_account_name"):
            print(
                f"Currently mapped in Actual Budget to: {existing_mapping['actual_account_name']}"
            )

        if f"{account_type}_do_not_map" in akahu_to_account_mapping.get(akahu_id, {}):
            print(f"Previously marked as DO NOT MAP for {account_type}")
        print(f"Here is a list of {account_type} accounts:")
        print(
            "(Press Enter to skip for now)"
        )  # Separate skip option  # User-friendly version  # Add this explicit option
        print(
            "0. Mark this account as DO NOT MAP (will not ask again)"
        )  # Explicit do-not-map option

        for target_account in target_accounts_list:
            account_id = target_account["id"]
            account_name = target_account["name"]
            seq = target_account["seq"]

            if any(
                account_id == mapping.get(target_account_key)
                for mapping in akahu_to_account_mapping.values()
            ):
                print(f"{seq}. {account_name} (Already Mapped)")
            else:
                print(f"{seq}. {account_name}")

        if suggested_index == 0:
            print("No suitable match found.")
        elif suggested_index is not None:
            matched_account = seq_to_acct(suggested_index, target_accounts_list)
            if matched_account is not None:
                print(f"Suggested match: {suggested_index}. {matched_account['name']}")
            else:
                print("No suitable match found.")
        else:
            logging.debug("No confident match found.")

        user_input = input(
            "Enter the number corresponding to the best match (or press Enter to skip): "
        )
        validated_index = validate_user_input(
            user_input,
            target_accounts_list,
            akahu_to_account_mapping,
            target_account_key,
        )

        if validated_index is None:
            if user_input != "":
                print("Invalid input.")
            continue
        elif validated_index == 0:
            # Mark as deliberately unmapped
            akahu_to_account_mapping.setdefault(akahu_id, {}).update(
                {
                    f"{account_type}_do_not_map": True,
                    "akahu_id": akahu_id,
                    "akahu_name": akahu_name,
                    f"{account_type}_matched_date": datetime.now().isoformat(),
                }
            )
            print(f"Marked '{akahu_name}' as DO NOT MAP for {account_type}.")
            continue
        else:
            selected_account = seq_to_acct(validated_index, target_accounts_list)
            selected_id = selected_account["id"]
            selected_name = selected_account["name"]

            # Determine account type based on the budget system
            if account_type == "ynab":
                new_account_type = (
                    "Tracking" if not selected_account.get("on_budget") else "On Budget"
                )
            else:  # actual
                new_account_type = (
                    "Tracking"
                    if selected_account.get("offbudget") == 1
                    else "On Budget"
                )

            # Check for existing account type if this account is already mapped
            existing_mapping = akahu_to_account_mapping.get(akahu_id, {})
            existing_type = existing_mapping.get("account_type")
            if existing_type and existing_type != new_account_type:
                logging.warning(
                    f"Account type mismatch for {akahu_name}: Previously {existing_type}, but {account_type} indicates {new_account_type}"
                )

            # Create or update the mapping
            mapping_update = {
                target_account_key: selected_id,
                target_account_name: selected_name,
                "akahu_id": akahu_id,
                "akahu_name": akahu_name,
                f"{account_type}_matched_date": datetime.now().isoformat(),
                "actual_budget_id": (
                    os.getenv("ACTUAL_SYNC_ID")
                    if account_type == "actual"
                    else existing_mapping.get("actual_budget_id")
                ),
                "ynab_budget_id": (
                    os.getenv("YNAB_BUDGET_ID")
                    if account_type == "ynab"
                    else existing_mapping.get("ynab_budget_id")
                ),
            }

            # Only set account_type if it's not already set or if this is YNAB (YNAB is authoritative for account type)
            if not existing_type or account_type == "ynab":
                mapping_update["account_type"] = new_account_type

            akahu_to_account_mapping.setdefault(akahu_id, {}).update(mapping_update)
            print(
                f"Mapped Akahu account '{akahu_name}' to target account '{selected_name}'."
            )

    return akahu_to_account_mapping


def check_for_changes(
    existing_akahu_accounts,
    latest_akahu_accounts,
    existing_actual_accounts,
    latest_actual_accounts,
    existing_ynab_accounts,
    latest_ynab_accounts,
):
    """Check for changes in account data between existing and latest versions."""
    akahu_accounts_match = True
    actual_accounts_match = True
    ynab_accounts_match = True

    # Check Akahu accounts
    if set(existing_akahu_accounts.keys()) != set(latest_akahu_accounts.keys()):
        akahu_accounts_match = False
    else:
        for key in existing_akahu_accounts:
            if not shallow_compare_dicts(
                existing_akahu_accounts[key], latest_akahu_accounts[key]
            ):
                akahu_accounts_match = False
                break

    # Check Actual accounts
    if set(existing_actual_accounts.keys()) != set(latest_actual_accounts.keys()):
        actual_accounts_match = False
    else:
        for key in existing_actual_accounts:
            if not shallow_compare_dicts(
                existing_actual_accounts[key], latest_actual_accounts[key]
            ):
                actual_accounts_match = False
                break

    # Check YNAB accounts
    if set(existing_ynab_accounts.keys()) != set(latest_ynab_accounts.keys()):
        ynab_accounts_match = False
    else:
        for key in existing_ynab_accounts:
            if not shallow_compare_dicts(
                existing_ynab_accounts[key], latest_ynab_accounts[key]
            ):
                ynab_accounts_match = False
                break

    return (akahu_accounts_match, actual_accounts_match, ynab_accounts_match)


ACTUAL_LINK_FIELDS = (
    "actual_account_id",
    "actual_account_name",
    "actual_budget_id",
    "actual_synced_datetime",
)
NEW_LINK_LOOKBACK_DAYS = 90
WATERMARK_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def apply_actual_links(
    mapping, linked_accounts, akahu_accounts, budget_id, now, keep_account_type=False
):
    """Rebuild the Actual side of the mapping from Actual's Bank Sync links.

    Actual is the source of truth: every Actual field is regenerated from
    `linked_accounts`, while Akahu, YNAB and Sure fields are left untouched.
    Entries Actual does not link are marked actual_do_not_map. A link to an
    Akahu account that Akahu no longer returns is skipped and reported at ERROR
    level so the remaining accounts still sync.

    Args:
        mapping: Existing mapping dict keyed by Akahu account ID.
        linked_accounts: Dicts with keys id, name, akahu_id, offbudget and
            newest_imported_date (a date or None) for each open Actual account
            linked to Akahu.
        akahu_accounts: Akahu accounts currently returned by Akahu, keyed by ID.
        budget_id: Value stored as actual_budget_id on each linked entry.
        now: Current UTC datetime.
        keep_account_type: Keep the existing account_type of entries linked to
            YNAB (YNAB owns it when YNAB sync is enabled).

    Returns:
        (updated_mapping, changes) where changes is a list of (level, message).
    """
    changes = []
    usable_links = []
    broken_actual_ids = set()
    broken_links = set()
    seen_akahu_ids = {}
    for account in linked_accounts:
        akahu_id = account["akahu_id"]
        if akahu_id not in akahu_accounts:
            changes.append((
                logging.ERROR,
                f"Actual account '{account['name']}' is linked to Akahu account "
                f"{akahu_id}, which Akahu no longer returns as active. Skipping it; "
                "relink it in Actual's Bank Sync page.",
            ))
            broken_actual_ids.add(account["id"])
            broken_links.add((account["id"], akahu_id))
            continue
        usable_links.append(account)
        if akahu_id in seen_akahu_ids:
            raise RuntimeError(
                f"Actual accounts '{seen_akahu_ids[akahu_id]}' and "
                f"'{account['name']}' are both linked to Akahu account {akahu_id}."
            )
        seen_akahu_ids[akahu_id] = account["name"]

    updated = {akahu_id: dict(entry) for akahu_id, entry in mapping.items()}
    previous = {}
    for akahu_id, entry in updated.items():
        actual_id = entry.get("actual_account_id")
        if actual_id:
            previous[actual_id] = {"akahu_id": akahu_id, **entry}
        if (actual_id, akahu_id) in broken_links:
            # Keep the watermark so syncing resumes once Akahu returns it again.
            continue
        for field in ACTUAL_LINK_FIELDS:
            entry.pop(field, None)

    now_text = now.strftime(WATERMARK_FORMAT)
    linked_actual_ids = set()
    for account in usable_links:
        akahu_id = account["akahu_id"]
        linked_actual_ids.add(account["id"])
        entry = updated.setdefault(
            akahu_id,
            {"akahu_id": akahu_id, "akahu_name": akahu_accounts[akahu_id].get("name")},
        )
        old = previous.get(account["id"])

        actual_type = "Tracking" if account["offbudget"] else "On Budget"
        old_type = entry.get("account_type") or (old or {}).get("account_type")
        if old_type and old_type != actual_type:
            if keep_account_type and entry.get("ynab_account_id"):
                changes.append((
                    logging.WARNING,
                    f"'{account['name']}' is {'off' if account['offbudget'] else 'on'}"
                    f"-budget in Actual but keeps account type {old_type} because "
                    "YNAB sync owns the account type.",
                ))
                actual_type = old_type
            else:
                changes.append((
                    logging.WARNING,
                    f"'{account['name']}' account type changed from {old_type} to "
                    f"{actual_type} (follows Actual's off-budget setting).",
                ))
        entry["account_type"] = actual_type

        if old and old.get("actual_synced_datetime"):
            watermark = old["actual_synced_datetime"]
        elif account["newest_imported_date"]:
            watermark = account["newest_imported_date"].strftime(WATERMARK_FORMAT)
        else:
            watermark = (now - timedelta(days=NEW_LINK_LOOKBACK_DAYS)).strftime(
                WATERMARK_FORMAT
            )

        if old is None:
            changes.append((
                logging.INFO,
                f"Linked '{account['name']}' to Akahu account {akahu_id}.",
            ))
            matched_date = now_text
        elif old["akahu_id"] != akahu_id:
            changes.append((
                logging.WARNING,
                f"Relinked '{account['name']}' from Akahu account "
                f"{old['akahu_id']} to {akahu_id}.",
            ))
            matched_date = now_text
        else:
            matched_date = old.get("actual_matched_date") or now_text

        entry.pop("actual_do_not_map", None)
        entry.update({
            "actual_account_id": account["id"],
            "actual_account_name": account["name"],
            "actual_budget_id": budget_id,
            "actual_matched_date": matched_date,
            "actual_synced_datetime": watermark,
        })

    for actual_id, old in previous.items():
        if actual_id not in linked_actual_ids | broken_actual_ids:
            updated[old["akahu_id"]]["actual_matched_date"] = now_text
            changes.append((
                logging.WARNING,
                f"'{old.get('actual_account_name', actual_id)}' is not linked in "
                "Actual's Bank Sync page; it will no longer sync to Actual.",
            ))

    linked_akahu_ids = {account["akahu_id"] for account in usable_links}
    for akahu_id, entry in updated.items():
        if akahu_id not in linked_akahu_ids:
            entry["actual_do_not_map"] = True

    return updated, changes


def refresh_actual_links(actual_client, mapping_file):
    """Regenerate the mapping file's Actual links from Actual.

    Returns (mapping, errors) where errors lists links that were skipped; the
    caller should fail the run after syncing the rest.
    """
    from actual.queries import get_accounts, get_transactions

    from modules.account_fetcher import fetch_akahu_accounts
    from modules.config import ENVs, RUN_SYNC_TO_YNAB
    from modules.mapping_store import load_existing_mapping, save_mapping

    akahu_accounts, actual_accounts, ynab_accounts, mapping = load_existing_mapping(
        mapping_file, generate_stub=True
    )
    watermarked_actual_ids = {
        entry["actual_account_id"]
        for entry in mapping.values()
        if entry.get("actual_account_id") and entry.get("actual_synced_datetime")
    }

    linked_accounts = []
    for account in get_accounts(actual_client.session):
        if account.closed or not account.account_sync_source:
            continue
        if account.account_sync_source.lower() != "akahu":
            logging.warning(
                f"Ignoring Actual account '{account.name}': linked to "
                f"{account.account_sync_source}, not Akahu."
            )
            continue
        if not account.account_id:
            raise RuntimeError(
                f"Actual account '{account.name}' is marked as Akahu-linked but has "
                "no Akahu account ID. Relink it in Actual's Bank Sync page."
            )
        newest_imported = None
        if account.id not in watermarked_actual_ids:
            newest_imported = next(
                (
                    t.get_date()
                    for t in get_transactions(actual_client.session, account=account)
                    if t.financial_id
                ),
                None,
            )
        linked_accounts.append({
            "id": account.id,
            "name": account.name,
            "akahu_id": account.account_id,
            "offbudget": bool(account.offbudget),
            "newest_imported_date": newest_imported,
        })

    if not linked_accounts:
        raise RuntimeError(
            "ACTUAL_ACCOUNT_LINKS=actual but no Actual accounts are linked to Akahu. "
            "Link them in Actual's Bank Sync page."
        )

    mapping, changes = apply_actual_links(
        mapping,
        linked_accounts,
        fetch_akahu_accounts(),
        ENVs["ACTUAL_SYNC_ID"],
        datetime.now(timezone.utc),
        keep_account_type=RUN_SYNC_TO_YNAB,
    )
    for level, message in changes:
        logging.log(level, message)
    logging.info(f"Using {len(linked_accounts)} account links from Actual.")

    save_mapping(
        {
            "akahu_accounts": akahu_accounts,
            "actual_accounts": actual_accounts,
            "ynab_accounts": ynab_accounts,
            "mapping": mapping,
        },
        mapping_file,
    )
    return mapping, [message for level, message in changes if level >= logging.ERROR]
