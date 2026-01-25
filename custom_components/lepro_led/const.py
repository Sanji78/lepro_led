DOMAIN = "lepro_led"

# API Regions - users need to select based on where they registered their account
API_REGIONS = {
    "eu": {
        "name": "Europe",
        "api_host": "api-eu-iot.lepro.com",
    },
    "fe": {
        "name": "Far East / Asia Pacific",
        "api_host": "api-fe-iot.lepro.com",
    },
    "us": {
        "name": "United States",
        "api_host": "api-us-iot.lepro.com",
    },
}

DEFAULT_REGION = "fe"

def get_api_host(region: str = DEFAULT_REGION) -> str:
    """Get the API host for a given region."""
    return API_REGIONS.get(region, API_REGIONS[DEFAULT_REGION])["api_host"]

def get_login_url(region: str = DEFAULT_REGION) -> str:
    return f"https://{get_api_host(region)}/user/login"

def get_family_list_url(region: str = DEFAULT_REGION) -> str:
    return f"https://{get_api_host(region)}/family/list/timestamp/{{timestamp}}"

def get_user_profile_url(region: str = DEFAULT_REGION) -> str:
    return f"https://{get_api_host(region)}/user/profile"

def get_device_list_url(region: str = DEFAULT_REGION) -> str:
    return f"https://{get_api_host(region)}/v3/device/list/fid/{{fid}}/timestamp/{{timestamp}}"

def get_switch_api_url(region: str = DEFAULT_REGION) -> str:
    return f"https://{get_api_host(region)}/statistic/record"

# Legacy URLs for backwards compatibility (using default region)
LOGIN_URL = get_login_url()
FAMILY_LIST_URL = get_family_list_url()
USER_PROFILE_URL = get_user_profile_url()
DEVICE_LIST_URL = get_device_list_url()
SWITCH_API_URL = get_switch_api_url()
