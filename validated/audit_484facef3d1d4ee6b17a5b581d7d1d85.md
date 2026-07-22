### Title
Swap Allowlist Bypassed via Router: `sender` Checked Is the Router, Not the Actual User — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the actual user. If the pool admin allowlists the router (a natural operational choice), every user — including those explicitly not on the allowlist — can bypass the swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter`, the router calls `pool.swap()`, so `sender = router address`. The allowlist check becomes `allowedSwapper[pool][router]`. If the admin has allowlisted the router (e.g., to let KYC'd users access the pool through the official periphery), the check passes for **every** user who routes through it, regardless of whether that user is individually allowlisted.

This is structurally identical to the external bug: just as `normalizedToFull()` substituted the global `totalSupply` for the user's actual balance, `beforeSwap` substitutes the global intermediary address (router) for the actual user address — producing a check that is always satisfied for any user behind the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the economic beneficiary of the position), not `sender` (the caller of `pool.addLiquidity`): [4](#0-3) 

The asymmetry is the root cause: deposits gate the right actor; swaps gate the wrong one when a router is in the path.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, drain LP value at oracle prices, or front-run allowlisted participants on a pool that was designed to be closed. This is a direct loss of LP assets and a broken core pool invariant (curated access).

---

### Likelihood Explanation

Allowlisting the router is a natural operational step: an admin who wants allowlisted users to access the pool through the official periphery must allowlist the router. The router is a public, permissionless contract with no internal access control of its own. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges.

---

### Recommendation

Gate the swap allowlist on the **originating user**, not the immediate caller of `pool.swap()`. Two options:

1. **Pass the real user through the router**: Have `MetricOmmSimpleRouter` accept a `swapper` parameter and forward it in `extensionData`; update `SwapAllowlistExtension.beforeSwap` to decode and check that address instead of `sender`.

2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a router, decode the real user from `extensionData` and check that address against the allowlist.

Either way, the invariant must be: the address checked against `allowedSwapper` is the address that economically benefits from the swap, not the address of the intermediary contract.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; configure it so only `alice` is allowlisted.
2. Admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — natural step so alice can use the official router.
3. bob (not allowlisted) calls router.exactInput(...) targeting the pool.
4. Router calls pool.swap(...); msg.sender inside pool = router.
5. _beforeSwap passes sender = router to SwapAllowlistExtension.
6. Check: allowedSwapper[pool][router] == true  →  passes.
7. bob's swap executes on the curated pool despite not being allowlisted.
``` [5](#0-4) [1](#0-0)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
