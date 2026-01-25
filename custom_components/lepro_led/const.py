DOMAIN = "lepro_led"

# Region configuration
REGIONS = {
    "eu": "api-eu-iot.lepro.com",
    "na": "api-na-iot.lepro.com",
}

# URL paths (combined with region base URL)
LOGIN_PATH = "/user/login"
FAMILY_LIST_PATH = "/family/list/timestamp/{timestamp}"
USER_PROFILE_PATH = "/user/profile"
DEVICE_LIST_PATH = "/v3/device/list/fid/{fid}/timestamp/{timestamp}"
SWITCH_API_PATH = "/statistic/record"

