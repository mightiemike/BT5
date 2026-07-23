### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Trader, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit any router-mediated swap on a curated pool), every user — including those explicitly excluded from the allowlist — can bypass the gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct for the pool-keyed mapping), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(address sender, ...) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

The pool populates `sender` with `msg.sender` of the `swap` call:

```solidity
_afterSwap(msg.sender, recipient, zeroForOne, ...);
``` [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap(...)`, `msg.sender` at the pool is the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

For any router-mediated swap to succeed on a curated pool, the admin must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every caller regardless of who the actual end user is, because the router's address is the only identity the extension ever sees.

The `DepositAllowlistExtension` does not share this flaw: it checks the explicit `owner` argument, which the pool passes through from the caller's supplied parameter rather than from `msg.sender`. [4](#0-3) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) provides no protection against any user who routes through `MetricOmmSimpleRouter`. Any disallowed address can execute swaps at oracle-derived prices, draining LP value or violating the pool's curation invariant. This is a direct loss of the policy guarantee the pool admin paid to enforce, and on pools with concentrated liquidity it can result in direct LP fund loss through adversarial swaps that the allowlist was meant to prevent. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router (the standard operational step to enable router-mediated swaps on a curated pool) and that at least one user is excluded from the per-address allowlist. Both conditions are the normal production state for any curated pool that also supports the periphery router. No privileged access, no malicious setup, and no non-standard tokens are required. [6](#0-5) 

---

### Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary. Two complementary fixes:

1. **Pool-side**: Pass the original user identity through the router as a dedicated `sender` argument rather than relying on `msg.sender`. The router already knows the caller and can forward it explicitly.
2. **Extension-side**: If the pool cannot be changed, the extension should reject calls from known router addresses unless the per-user identity is embedded in `extensionData` and verified there.

The deposit-side pattern — using an explicit `owner` parameter that the pool accepts from the caller — is the correct model and should be mirrored on the swap path. [4](#0-3) 

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps (standard operational step).
3. Pool admin calls `setAllowedToSwap(pool, attacker, false)` (or simply never allowlists the attacker).
4. Attacker calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
5. The router calls `pool.swap(attacker_recipient, ...)` — `msg.sender` at the pool is the router.
6. `_beforeSwap` passes `sender = router` to the extension.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Attacker receives tokens at oracle price despite being excluded from the allowlist. [5](#0-4) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L281-295)
```text
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );
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
