# Module for handling account mapping logic
import json
import logging
from datetime import datetime
import openai
import os
from fuzzywuzzy import process

# OpenAI client will be initialized in functions that need it


def is_simple_value(value):
    """Check if the value is a trivial type: int, float, str, bool, or None"""
    return isinstance(value, (int, float, str, bool)) or value is None


def shallow_compare_dicts(dict1, dict2):
    """Compare dictionaries using only simple values"""
    dict1_filtered = {k: v for k, v in dict1.items() if is_simple_value(v)}
    dict2_filtered = {k: v for k, v in dict2.items() if is_simple_value(v)}
    return dict1_filtered == dict2_filtered


def generate_mapping_stub(mapping_file="akahu_budget_mapping.json"):
    """Generate a stub JSON file for the mapping."""
    stub = {
        "akahu_accounts": {},
        "actual_accounts": {},
        "ynab_accounts": {},
        "mapping": {},
    }
    with open(mapping_file, "w") as f:
        json.dump(stub, f, indent=4)
    print(f"Stub mapping file created: {mapping_file}")


def load_existing_mapping(
    mapping_file="akahu_budget_mapping.json", generate_stub=False
):
    """Load existing mapping from JSON file"""
    try:
        with open(mapping_file, "r") as f:
            data = json.load(f)
            # Validate required fields
            required_fields = [
                "akahu_accounts",
                "actual_accounts",
                "ynab_accounts",
                "mapping",
            ]
            if not all(field in data for field in required_fields):
                raise ValueError(
                    f"Mapping file missing required fields: {required_fields}"
                )

            mapping = data.get("mapping", {})
            if isinstance(mapping, list):
                mapping = {
                    entry["akahu_id"]: entry for entry in mapping if "akahu_id" in entry
                }
            return (
                data["akahu_accounts"],
                data["actual_accounts"],
                data["ynab_accounts"],
                mapping,
            )
    except FileNotFoundError:
        logging.warning("Mapping file not found - first run ever?")
        generate_mapping_stub(mapping_file=mapping_file)
        return load_existing_mapping(mapping_file=mapping_file, generate_stub=False)
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON in mapping file {mapping_file}")


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

            # Process Actual deletions
            for actual_id, akahu_id in [(aid, aid) for aid in deleted_actual_accounts]:
                if (
                    akahu_id in updated_mapping
                    and updated_mapping[akahu_id].get("actual_account_id") == actual_id
                ):
                    for key in [
                        "actual_account_id",
                        "actual_budget_id",
                        "actual_budget_name",
                        "actual_account_name",
                    ]:
                        updated_mapping[akahu_id].pop(key, None)
                    logging.info(
                        f"Removed Actual account {actual_id} from mapping {akahu_id}."
                    )

            # Process YNAB deletions
            for ynab_id, akahu_id in [(yid, aid) for yid in deleted_ynab_accounts]:
                if (
                    akahu_id in updated_mapping
                    and updated_mapping[akahu_id].get("ynab_account_id") == ynab_id
                ):
                    for key in [
                        "ynab_account_id",
                        "ynab_budget_id",
                        "ynab_budget_name",
                        "ynab_account_name",
                    ]:
                        updated_mapping[akahu_id].pop(key, None)
                    logging.info(
                        f"Removed YNAB account {ynab_id} from mapping {akahu_id}."
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
        client = openai.OpenAI(
            base_url=os.getenv("OPENAI_BASE_URL", None),
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", default="gpt-4"),
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
        logging.error(f"OpenAI API call failed or gave an invalid response: {str(e)}")

    return get_fuzzy_match_suggestion(
        akahu_account, target_accounts, akahu_to_account_mapping, target_account_key
    )


def get_fuzzy_match_suggestion(
    akahu_account, target_accounts, akahu_to_account_mapping, target_account_key
):
    """Get account match suggestion using fuzzy matching."""
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


def remove_seq(data):
    """Recursively removes 'seq' keys from dictionaries while preserving structure."""
    if isinstance(data, dict):
        return {key: remove_seq(value) for key, value in data.items() if key != "seq"}
    elif isinstance(data, list):
        return [remove_seq(item) for item in data]
    else:
        return data


def save_mapping(data_to_save, mapping_file="akahu_budget_mapping.json"):
    """Saves the mapping along with Akahu, Actual, and YNAB accounts to a JSON file."""
    try:
        serialized_data = json.dumps(data_to_save, indent=4)
        data_dict = json.loads(serialized_data)
        required_keys = {
            "akahu_accounts",
            "actual_accounts",
            "ynab_accounts",
            "mapping",
        }

        if not required_keys.issubset(data_dict.keys()):
            raise ValueError(
                f"Serialized data is missing one or more required keys: {required_keys - data_dict.keys()}"
            )

        with open(mapping_file, "w") as f:
            f.write(serialized_data)
    except Exception as e:
        logging.error(f"Failed to save mapping: {e}")


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
