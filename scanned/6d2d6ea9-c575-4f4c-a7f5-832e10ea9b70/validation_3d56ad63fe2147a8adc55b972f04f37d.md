### Title
`SwapAllowlistExtension` checks router address instead of end-user identity, enabling full allowlist bypass through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the first argument passed by the pool — which is the pool's own `msg.sender` (the direct caller of `pool.swap()`). When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (used as the namespace key) and `sender` is the swapper identity the pool forwards. The pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender` to the extension. This is confirmed by the integration test, which allowlists `address(callers[0])` (a `TestCaller` wrapper contract), not the underlying EOA `users[0]`:

```solidity
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
``` [2](#0-1) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The router stores the real payer only in transient storage for the payment callback — it is never forwarded to the pool as the swapper identity. [3](#0-2) 

This creates an irreconcilable dilemma for any pool admin who deploys `SwapAllowlistExtension` to curate access:

| Admin intent | Admin action | Outcome |
|---|---|---|
| Restrict to specific users, support router | Allowlist individual users + allowlist router | **All users bypass the allowlist via the router** |
| Restrict to specific users, block router | Allowlist individual users only | **Allowlisted users cannot use the standard periphery path** |

The protocol's own audit target description confirms this is the exact concern:

> *"Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting."* [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institution-only) using `SwapAllowlistExtension` and also allowlists the router to support the standard periphery path inadvertently opens the pool to all users. Any non-allowlisted address can call `MetricOmmSimpleRouter.exactInput` / `exactOutput` and the extension will check the router's address (which is allowlisted), not the caller's address. The individual per-user allowlist is completely defeated. This is a direct policy bypass on a production pool with real LP assets and swap fees at stake — matching the "allowlist bypass" and "wrong-actor binding" impact categories. [5](#0-4) 

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary supported periphery swap path; most users will use it.
- Any pool admin who wants both access control and router support will naturally allowlist the router, triggering the bypass.
- No special privileges, malicious setup, or non-standard tokens are required — a standard router call from any EOA is sufficient.
- The bypass is reachable in a single transaction with no preconditions beyond the pool being deployed with `SwapAllowlistExtension` and the router being allowlisted.

---

### Recommendation

The pool must forward the economically relevant actor — the end user — as `sender` to the extension, not the router's address. Two concrete options:

1. **Router forwards user identity**: `MetricOmmSimpleRouter` passes the original `msg.sender` (the user) as an explicit `sender` argument to `pool.swap()`, and the pool forwards it to extensions instead of its own `msg.sender`.
2. **Extension reads from extensionData**: The router encodes the user's address in `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires the router to always populate this field for allowlisted pools.

Option 1 is cleaner and preserves the extension interface contract. Option 2 is fragile because it depends on the router correctly populating `extensionData` for every hop.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // allowlist Alice
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router for periphery support
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
   → router calls pool.swap(recipient=Bob, ...)
   → pool passes msg.sender=router as `sender` to extension
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes — Bob bypassed the allowlist
```

The extension's check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][router]` — always true once the router is allowlisted — regardless of which EOA initiated the router call. [1](#0-0) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```

**File:** metric-periphery/contracts/libraries/TransientCallbackPool.sol (L66-79)
```text
  function getPayer() internal view returns (address payer) {
    payer = _tloadAddress(T_PAYER_SLOT);
  }

  function getTokenToPay() internal view returns (address tokenToPay) {
    tokenToPay = _tloadAddress(T_TOKEN_TO_PAY_SLOT);
  }

  function clear() internal {
    _tstore(T_SLOT, 0);
    _tstore(T_AMOUNT_IN_SLOT, 0);
    _tstoreAddress(T_PAYER_SLOT, address(0));
    _tstoreAddress(T_TOKEN_TO_PAY_SLOT, address(0));
  }
```

**File:** generate_scanned_questions.py (L656-663)
```python
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```

**File:** generate_scanned_questions.py (L718-724)
```python
        Vector(
            title="wrong-actor binding",
            question_focus="the hook checks the wrong actor among sender, owner, payer, or recipient",
            exploit="Separate payer from owner or route through the router so the extension sees a different actor than the protocol intended to gate.",
            invariant="Every guard must key authorization to the same actor that the economic action is actually attributed to.",
            impact="High direct loss or policy bypass on curated pools.",
        ),
```
