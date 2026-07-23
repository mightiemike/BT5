The call path is fully traceable in the scoped code. Let me confirm the exact binding at each step.

The call path is fully confirmed. Here is the exact binding at each step:

**Step 1** — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no `sender` forwarding: [1](#0-0) 

**Step 2** — `MetricOmmPool.swap` passes `msg.sender` (= the router address) as `sender` to `_beforeSwap`: [2](#0-1) 

**Step 3** — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router address — the original user's address is never consulted: [3](#0-2) 

The invariant the extension is supposed to enforce — gate the economically acting user — is broken for every router-mediated swap. Two concrete failure modes exist simultaneously:

- **Bypass**: If the pool admin allowlists the router address (a natural and expected action to enable router-based swaps on a curated pool), every user on the internet can swap through the router regardless of whether they are individually allowlisted.
- **Broken functionality**: If the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all, breaking the supported periphery path.

Neither failure mode requires a malicious admin or non-standard token. The router is a public, permissionless contract and the allowlist check is structurally wrong for any router-mediated call.

---

### Title
SwapAllowlistExtension checks router address instead of original user, allowing allowlist bypass through MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` = router address. The extension therefore checks whether the router is allowlisted, not whether the original user is allowlisted. Any user can bypass a curated pool's per-user allowlist by calling `router.exactInputSingle` (or any other router entry point) instead of calling `pool.swap` directly.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(msg.sender, recipient, ...);
```

When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, `msg.sender` inside the pool is the router contract address. `ExtensionCalling._beforeSwap` encodes this router address as `sender` and forwards it to `SwapAllowlistExtension.beforeSwap`. The extension then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The lookup is `allowedSwapper[pool][router]`. The original transaction initiator's address is never read. If the pool admin has allowlisted the router (to permit router-based swaps), the check passes for every caller regardless of their individual allowlist status.

### Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no actual restriction for router-mediated swaps. Any address can call `router.exactInputSingle` and execute a swap on the pool. This is a direct curation failure: the pool's intended access control is bypassed, allowing unauthorized parties to trade against the pool's liquidity. Depending on pool design, this can result in unauthorized price impact, fee extraction, or violation of regulatory/compliance requirements that the allowlist was meant to enforce.

### Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary supported periphery swap path. Pool admins who want to allow router-based swaps on a curated pool must allowlist the router address — there is no other mechanism. Once the router is allowlisted, the bypass is unconditional and requires no special setup by the attacker beyond calling the public router. The likelihood is high for any curated pool that intends to support router-based swaps.

### Recommendation
The `sender` passed to extension hooks must represent the original transaction initiator, not the intermediate caller. Two approaches:

1. **Pass `tx.origin` as `sender`** — simple but incompatible with smart-contract wallets and multi-sig flows.
2. **Router forwards original caller** — `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` and `SwapAllowlistExtension` reads it from there, verifying the pool is the caller via `msg.sender`. This preserves composability.
3. **Allowlist check at pool level** — add a `SWAP_ALLOWLIST_PROVIDER` check in `MetricOmmPool.swap` that reads the original `msg.sender` before any extension dispatch, similar to how `NotAllowedToSwap` is documented in the pool interface.

The cleanest fix consistent with the existing architecture is option 3: move the allowlist check into the pool itself (keyed on `msg.sender`) rather than delegating it to an extension that only sees the proxied `sender`.

### Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension active
// 2. Admin allowlists only the router: swapExt.setAllowedToSwap(pool, address(router), true)
// 3. Unlisted user calls router directly
vm.prank(unlistedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    ...
}));
// Swap succeeds — beforeSwap received sender=router (allowlisted), not unlistedUser

// 4. Same unlisted user calls pool directly
vm.prank(unlistedUser);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(unlistedUser, true, 1000, 0, "", "");
// Reverts correctly — beforeSwap received sender=unlistedUser (not allowlisted)
```

The asymmetry proves the bypass: the same user is blocked on the direct path but succeeds through the router.

### Citations

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
