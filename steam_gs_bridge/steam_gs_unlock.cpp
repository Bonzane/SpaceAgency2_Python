#include <steam/steam_api.h>
#include <steam/steam_gameserver.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

namespace {
struct Args {
    uint64_t steam_id = 0;
    std::string achievement;
    std::string product = "SpaceAgency2";
    std::string game_desc = "Space Agency 2";
    std::string mod_dir = "spaceagency2";
    std::string server_name = "SpaceAgency2 GS";
    std::string version = "1.0.0.0";
    uint32_t ip = 0;
    uint16_t game_port = 0;
    uint16_t query_port = 0;
    int server_mode = eServerModeAuthentication;
    int timeout_ms = 8000;
};

bool parse_arg(int argc, char** argv, const char* key, std::string& out) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::strcmp(argv[i], key) == 0) {
            out = argv[i + 1];
            return true;
        }
    }
    return false;
}

bool parse_arg_int(int argc, char** argv, const char* key, int& out) {
    std::string val;
    if (!parse_arg(argc, argv, key, val)) {
        return false;
    }
    out = std::atoi(val.c_str());
    return true;
}

bool parse_arg_u64(int argc, char** argv, const char* key, uint64_t& out) {
    std::string val;
    if (!parse_arg(argc, argv, key, val)) {
        return false;
    }
    out = static_cast<uint64_t>(std::strtoull(val.c_str(), nullptr, 10));
    return true;
}

bool parse_arg_u16(int argc, char** argv, const char* key, uint16_t& out) {
    int tmp = 0;
    if (!parse_arg_int(argc, argv, key, tmp)) {
        return false;
    }
    out = static_cast<uint16_t>(tmp);
    return true;
}

bool parse_arg_u32(int argc, char** argv, const char* key, uint32_t& out) {
    int tmp = 0;
    if (!parse_arg_int(argc, argv, key, tmp)) {
        return false;
    }
    out = static_cast<uint32_t>(tmp);
    return true;
}

void usage() {
    std::cerr << "Usage: steam_gs_unlock --steamid <id64> --achievement <name> --app-id <appid>\n";
}

class StatsRequest {
  public:
    void Start(CSteamID steam_id) {
        SteamAPICall_t call = SteamGameServerStats()->RequestUserStats(steam_id);
        m_call.Set(call, this, &StatsRequest::OnStatsReceived);
    }
    bool done = false;
    bool ok = false;
  private:
    void OnStatsReceived(GSStatsReceived_t* p, bool io_failure) {
        done = true;
        ok = !io_failure && p && p->m_eResult == k_EResultOK;
    }
    CCallResult<StatsRequest, GSStatsReceived_t> m_call;
};

class StatsStore {
  public:
    void Start(CSteamID steam_id) {
        SteamAPICall_t call = SteamGameServerStats()->StoreUserStats(steam_id);
        m_call.Set(call, this, &StatsStore::OnStatsStored);
    }
    bool done = false;
    bool ok = false;
  private:
    void OnStatsStored(GSStatsStored_t* p, bool io_failure) {
        done = true;
        ok = !io_failure && p && p->m_eResult == k_EResultOK;
    }
    CCallResult<StatsStore, GSStatsStored_t> m_call;
};
}  // namespace

int main(int argc, char** argv) {
    Args args;
    if (!parse_arg_u64(argc, argv, "--steamid", args.steam_id)) {
        usage();
        return 2;
    }
    if (!parse_arg(argc, argv, "--achievement", args.achievement)) {
        usage();
        return 2;
    }
    std::string app_id_str;
    if (!parse_arg(argc, argv, "--app-id", app_id_str)) {
        usage();
        return 2;
    }
    if (!app_id_str.empty()) {
        setenv("SteamAppId", app_id_str.c_str(), 1);
    }

    parse_arg_u32(argc, argv, "--ip", args.ip);
    parse_arg_u16(argc, argv, "--game-port", args.game_port);
    parse_arg_u16(argc, argv, "--query-port", args.query_port);
    parse_arg(argc, argv, "--product", args.product);
    parse_arg(argc, argv, "--game-desc", args.game_desc);
    parse_arg(argc, argv, "--mod-dir", args.mod_dir);
    parse_arg(argc, argv, "--server-name", args.server_name);
    parse_arg(argc, argv, "--version", args.version);
    parse_arg_int(argc, argv, "--server-mode", args.server_mode);
    parse_arg_int(argc, argv, "--timeout-ms", args.timeout_ms);

    if (args.game_port == 0) {
        args.game_port = 27015;
    }
    if (args.query_port == 0) {
        args.query_port = 27016;
    }

    SteamErrMsg err_msg = {};
    ESteamAPIInitResult init_result = SteamGameServer_InitEx(
        args.ip,
        args.game_port,
        args.query_port,
        static_cast<EServerMode>(args.server_mode),
        args.version.c_str(),
        &err_msg
    );
    if (init_result != k_ESteamAPIInitResult_OK) {
        std::cerr << "init_failed: " << err_msg << "\n";
        return 1;
    }

    SteamGameServer()->SetProduct(args.product.c_str());
    SteamGameServer()->SetGameDescription(args.game_desc.c_str());
    SteamGameServer()->SetModDir(args.mod_dir.c_str());
    SteamGameServer()->SetDedicatedServer(true);
    SteamGameServer()->SetServerName(args.server_name.c_str());

    SteamGameServer()->LogOnAnonymous();

    auto start = std::chrono::steady_clock::now();
    while (!SteamGameServer()->BLoggedOn()) {
        SteamGameServer_RunCallbacks();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start
        ).count();
        if (elapsed > args.timeout_ms) {
            std::cerr << "logon_timeout\n";
            SteamGameServer_Shutdown();
            return 1;
        }
    }

    CSteamID steam_id(static_cast<uint64>(args.steam_id));
    StatsRequest req;
    req.Start(steam_id);
    while (!req.done) {
        SteamGameServer_RunCallbacks();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start
        ).count();
        if (elapsed > args.timeout_ms) {
            std::cerr << "stats_request_timeout\n";
            SteamGameServer_Shutdown();
            return 1;
        }
    }
    if (!req.ok) {
        std::cerr << "stats_request_failed\n";
        SteamGameServer_Shutdown();
        return 1;
    }

    if (!SteamGameServerStats()->SetUserAchievement(steam_id, args.achievement.c_str())) {
        std::cerr << "set_achievement_failed\n";
        SteamGameServer_Shutdown();
        return 1;
    }

    StatsStore store;
    store.Start(steam_id);
    while (!store.done) {
        SteamGameServer_RunCallbacks();
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start
        ).count();
        if (elapsed > args.timeout_ms) {
            std::cerr << "store_timeout\n";
            SteamGameServer_Shutdown();
            return 1;
        }
    }
    if (!store.ok) {
        std::cerr << "store_failed\n";
        SteamGameServer_Shutdown();
        return 1;
    }

    std::cout << "ok\n";
    SteamGameServer_Shutdown();
    return 0;
}
