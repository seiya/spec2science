// The test program of `test_harness_capture_groups.py`: a hand-written realization of what
// `harness_cpp_gpu`'s `controlled_spec.md` §3 states of the self-test's
// `l0_capture_groups_pass` case, over the hand-written model of `data/driver_e2e/` and the
// host-rendered header. Every other case id commits the placeholder `final` record a case of
// the suite commits for the variables of this case. Host code only: no kernel, so no device is
// needed. It is a test fixture, not a certified source: it shows the case's predicates hold on
// a model that follows the contract and fail on one that does not.
#include "harness_cpp_gpu_model.cuh"

#include <cstdlib>
#include <string>
#include <vector>

using namespace harness_cpp_gpu_model;

namespace {

const std::vector<std::string> series_names = {"g_a", "g_series_long"};

std::vector<harness_cpp_gpu__h_var> variables(int n_a, int n_b) {
    return {
        {"g_s", 0, {0, 0, 0, 0}, n_a, true},
        {"g_r1", 1, {3, 0, 0, 0}, n_a, true},
        {"g_r2", 2, {2, 3, 0, 0}, n_a, true},
        {"g_r3", 3, {2, 3, 4, 0}, n_b, true},
        {"g_r4", 4, {2, 3, 4, 5}, n_b, true},
        {"g_never", 1, {2, 0, 0, 0}, 0, false},
        {"g_z1", 1, {0, 0, 0, 0}, n_a, true},
        {"g_z2", 2, {3, 0, 0, 0}, n_b, true},
    };
}

// The elements of `a`, each plus `d`.
std::vector<dp> plus(const std::vector<dp>& a, dp d) {
    std::vector<dp> out(a);
    for (dp& x : out) {
        x += d;
    }
    return out;
}

template <int R>
spec2science::View<const dp, R> view(const std::vector<dp>& data, const long* extent) {
    spec2science::View<const dp, R> v{data.data(), {}};
    for (int k = 0; k < R; ++k) {
        v.extent[k] = extent[k];
    }
    return v;
}

const long z1_extent[1] = {0};
const long z2_extent[2] = {3, 0};
const long never_extent[1] = {2};

void groups_case(const std::string& case_id) {
    bool read_ok = false;
    harness_cpp_gpu__read_case(case_id, read_ok);
    if (!read_ok) {
        std::exit(4);
    }
    const int n = harness_cpp_gpu__get_int("inputs.initial.g_n");
    const int every_a = harness_cpp_gpu__get_int("inputs.initial.g_every_a");
    const int every_b = harness_cpp_gpu__get_int("inputs.initial.g_every_b");
    const int every_p = harness_cpp_gpu__get_int("inputs.initial.g_every_p");
    const dp dt = harness_cpp_gpu__get_real("inputs.initial.g_dt");
    const dp s = harness_cpp_gpu__get_real("inputs.initial.g_s");
    std::vector<dp> r1;
    spec2science::Array<dp, 2> r2;
    spec2science::Array<dp, 3> r3;
    spec2science::Array<dp, 4> r4;
    harness_cpp_gpu__get_array_r1("inputs.initial.g_r1", r1);
    harness_cpp_gpu__get_array_r2("inputs.initial.g_r2", r2);
    harness_cpp_gpu__get_array_r3("inputs.initial.g_r3", r3);
    harness_cpp_gpu__get_array_r4("inputs.initial.g_r4", r4);
    const long r1_extent[1] = {static_cast<long>(r1.size())};
    const std::vector<dp> never(2, 0.0);
    const std::vector<dp> none;

    // The variables of step `d`'s record (the bases themselves at `d = 0`), put into `group`.
    auto put_a = [&](const std::string& group, dp d) {
        harness_cpp_gpu__capture_real(group, "g_s", s + d);
        const std::vector<dp> a1 = plus(r1, d), a2 = plus(r2.data, d);
        harness_cpp_gpu__capture_array_r1(group, "g_r1", view<1>(a1, r1_extent));
        harness_cpp_gpu__capture_array_r2(group, "g_r2", view<2>(a2, r2.extent));
        harness_cpp_gpu__capture_array_r1(group, "g_z1", view<1>(none, z1_extent));
    };
    auto put_b = [&](const std::string& group, dp d) {
        const std::vector<dp> a3 = plus(r3.data, d), a4 = plus(r4.data, d);
        harness_cpp_gpu__capture_array_r3(group, "g_r3", view<3>(a3, r3.extent));
        harness_cpp_gpu__capture_array_r4(group, "g_r4", view<4>(a4, r4.extent));
        harness_cpp_gpu__capture_array_r2(group, "g_z2", view<2>(none, z2_extent));
    };
    auto put_all = [&](const std::string& group, dp d) {
        put_a(group, d);
        put_b(group, d);
        harness_cpp_gpu__capture_array_r1(group, "g_never", view<1>(never, never_extent));
    };

    harness_cpp_gpu__capture_open(case_id, variables(n / every_a, n / every_b), n / every_p,
                                  series_names, n);
    put_all("initial", 0.0);
    harness_cpp_gpu__capture_commit("initial", 0, 0.0);
    for (int k = 1; k <= n; ++k) {
        const dp d = static_cast<dp>(k);
        const dp t = dt * d;
        harness_cpp_gpu__capture_real("series", "g_a", s + d);
        harness_cpp_gpu__capture_real("series", "g_series_long", r1[0] - d);
        harness_cpp_gpu__capture_commit("series", k, t);
        const bool due_a = k % every_a == 0;
        const bool due_b = k % every_b == 0;
        if (due_a) {
            put_a("history", d);
        }
        if (due_b) {
            put_b("history", d);
        }
        if (due_a || due_b) {
            harness_cpp_gpu__capture_commit("history", k, t);
        }
        if (k % every_p == 0) {
            put_a("restart_point", d);
            put_b("restart_point", d);
            harness_cpp_gpu__capture_commit("restart_point", k, t);
        }
    }
    const dp d = static_cast<dp>(n);
    put_all("final", d);
    harness_cpp_gpu__capture_commit("final", n, dt * d);
    harness_cpp_gpu__capture_close();
}

// A case of another aspect: every variable of this case as a placeholder in its one `final`
// record, the same series names, and no row.
void placeholder_case(const std::string& case_id) {
    const std::vector<dp> zeros(120, 0.0), none;
    const long e1[1] = {3}, e2[2] = {2, 3}, e3[3] = {2, 3, 4}, e4[4] = {2, 3, 4, 5};
    harness_cpp_gpu__capture_open(case_id, variables(0, 0), 0, series_names, 0);
    harness_cpp_gpu__capture_real("final", "g_s", 0.0);
    harness_cpp_gpu__capture_array_r1("final", "g_r1", view<1>(zeros, e1));
    harness_cpp_gpu__capture_array_r2("final", "g_r2", view<2>(zeros, e2));
    harness_cpp_gpu__capture_array_r3("final", "g_r3", view<3>(zeros, e3));
    harness_cpp_gpu__capture_array_r4("final", "g_r4", view<4>(zeros, e4));
    harness_cpp_gpu__capture_array_r1("final", "g_never", view<1>(zeros, never_extent));
    harness_cpp_gpu__capture_array_r1("final", "g_z1", view<1>(none, z1_extent));
    harness_cpp_gpu__capture_array_r2("final", "g_z2", view<2>(none, z2_extent));
    harness_cpp_gpu__capture_commit("final", 1, 0.0);
    harness_cpp_gpu__capture_close();
}

}  // namespace

int main(int argc, char** argv) {
    const std::vector<std::string> tokens(argv + 1, argv + argc);
    std::vector<std::string> case_ids;
    int ncases = 0;
    bool ok = false;
    harness_cpp_gpu__parse_cases(tokens, argc - 1, case_ids, ncases, ok);
    if (!ok) {
        return 3;
    }
    for (const std::string& case_id : case_ids) {
        if (case_id == "l0_capture_groups_pass") {
            groups_case(case_id);
        } else {
            placeholder_case(case_id);
        }
    }
    return 0;
}
