### Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. The allowlist therefore gates the router address, not the actual swapper, making the allowlist either trivially bypassable (if the router is allowlisted) or broken for all legitimate router users (if it is not).

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract calling the extension hook) and `sender` is the first argument forwarded by the pool — the value of `msg.sender` inside `pool.swap()` at the time the hook fires.

`MetricOmmSimpleRouter` calls `pool.swap(recipient, ...)` directly. When it does so, `msg.sender` inside the pool is the router contract address. `ExtensionCalling._beforeSwap` then forwards that value as the `sender` argument to every registered extension.

The allowlist lookup therefore becomes:

```
allowedSwapper[pool][router_address]
```

not

```
allowedSwapper[pool][actual_end_user]
```

A pool admin who intends to restrict swaps to a curated set of addresses configures the allowlist with individual user addresses. Those entries are never matched when users route through the public router, because the extension sees only the router's address. Two exploitable outcomes follow:

1. **Bypass**: If the pool admin allowlists the router address (a natural mistake when trying to permit router-mediated swaps), every user — including those the admin intended to block — can swap freely by routing through `MetricOmmSimpleRouter`.
2. **Broken gate**: If the router is not allowlisted, every allowlisted user is silently blocked from using the standard periphery path, even though they hold explicit permission.

`DepositAllowlistExtension.beforeAddLiquidity` does not share this flaw because `addLiquidity` accepts an explicit `owner` parameter that the liquidity adder sets to the real user's address. Swaps have no equivalent explicit-user parameter; the only identity the pool forwards is `msg.sender`.

---

### Impact Explanation

**Direct loss of user principal / broken core pool functionality.**

- **Bypass path**: A non-allowlisted user calls `MetricOmmSimpleRouter.exactInput` (or any `exact*` entry point) targeting a curated pool whose admin has allowlisted the router. The extension passes, the swap executes, and LP funds are consumed by an actor the pool was designed to exclude. On a pool with concentrated liquidity and a narrow allowlist (e.g., a private market-making pool), this directly drains LP principal.
- **Broken path**: Allowlisted users cannot use the standard router at all, making the pool's primary swap interface unusable for its intended participants. This constitutes broken core pool functionality.

Both outcomes exceed Sherlock Medium/High thresholds: the bypass enables unauthorized extraction of LP assets; the broken gate renders the pool's swap flow unusable for legitimate users.

---

### Likelihood Explanation

**High.** The trigger requires no privilege. Any user can call `MetricOmmSimpleRouter` — it is a public, permissionless periphery contract. The bypass is reachable on every pool that uses `SwapAllowlistExtension` and has the router in its allowlist (or intends to permit router-mediated swaps). The broken-gate variant affects every allowlisted user who uses the standard router path, which is the documented primary swap entrypoint.

---

### Recommendation

The extension must identify the economic actor, not the immediate caller. Two approaches:

1. **Pass the real user via `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it. This requires a trusted router convention and is fragile if other routers are added.

2. **Check `recipient` instead of `sender` for router flows, or add an explicit `swapper` field**: Redesign the `beforeSwap` hook signature to include a dedicated `swapper` address that the pool sets to `tx.origin` or to a value the caller explicitly declares and the pool validates. The cleanest fix is for the pool to pass the original `msg.sender` of the top-level call, not the immediate caller, or to require the router to declare the real user in a verified field.

The allowlist check should be:

```solidity
// intended behavior
if (!allowAllSwappers[pool] && !allowedSwapper[pool][real_end_user]) {
    revert NotAllowedToSwap();
}
```

where `real_end_user` is the address the pool admin actually intends to gate.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users only,
     or mistakenly thinking this is how to enable the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. Attacker (non-allowlisted) calls MetricOmmSimpleRouter.exactInput(...)
     targeting the curated pool.
  2. Router calls pool.swap(attacker_as_recipient, ...).
     msg.sender inside pool = router_address.
  3. Pool calls SwapAllowlistExtension.beforeSwap(router_address, ...).
  4. Extension checks: allowedSwapper[pool][router_address] == true  ✓
  5. Swap executes. Attacker receives tokens from LP reserves.

Result:
  Non-allowlisted attacker successfully swaps on a curated pool,
  extracting LP principal that the allowlist was designed to protect.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L1-1)
```text
// SPDX-License-Identifier: MIT
```
