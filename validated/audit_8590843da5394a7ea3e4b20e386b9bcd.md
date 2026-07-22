### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` enforces its per-pool allowlist against `sender`, which is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract, not the user. If the pool admin allowlists the router (the only way to let legitimate users trade through it), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then enforces the allowlist against that `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router is the direct caller of `pool.swap()`: [4](#0-3) 

So `sender` arriving at the extension is the **router address**, not the user. The allowlist check becomes `allowedSwapper[pool][router]`.

The pool admin faces an impossible choice:

| Admin configuration | Effect |
|---|---|
| Allowlist only individual user addresses | Those users cannot trade through the router (router is not allowlisted); the standard periphery path is broken for them. |
| Allowlist the router address | Every unprivileged address bypasses the allowlist by routing through the router. |
| Allowlist individual users **and** the router | Same as above — the router entry is open to all. |

There is no configuration that simultaneously allows legitimate users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses provides no real restriction once the router is allowlisted. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`) and trade freely on the pool. The allowlist — the sole access-control boundary on the swap path — is silently defeated. This constitutes a broken core pool functionality and an admin-boundary break reachable by an unprivileged path, both within the contest's allowed impact gate.

---

### Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool admin who wants legitimate users to trade through the router must allowlist it. The bypass is then immediately available to every address with no additional preconditions, no privileged role, and no special token behavior. Likelihood is **high** for any pool that uses the allowlist extension alongside the router.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the immediate caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router, so the router address itself must be verified (e.g., via a factory lookup) before trusting the embedded identity.

2. **Add a dedicated `swapOriginator` field to the hook signature**: The pool passes both `msg.sender` (the immediate caller) and a separately tracked originator. Extensions can then choose which actor to gate.

Until fixed, pools that require a genuine swap allowlist must instruct users to call `pool.swap()` directly and must **not** allowlist the router address.

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured on beforeSwap.
2. Admin allowlists the router so that legitimate users can trade:
       extension.setAllowedToSwap(pool, address(router), true)
3. Attacker (not individually allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})
4. Router calls pool.swap(); msg.sender inside pool = router.
5. Pool calls extension.beforeSwap(router, recipient, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true  →  passes.
7. Swap executes. Attacker receives output tokens.
   The allowlist provided zero protection.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
