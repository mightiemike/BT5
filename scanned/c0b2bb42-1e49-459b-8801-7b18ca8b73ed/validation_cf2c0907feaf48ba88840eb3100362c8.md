### Title
SwapAllowlistExtension Checks Router Address Instead of Ultimate User, Allowing Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension checks the router's address, not the ultimate user's address. If the pool admin allowlists the router (a natural configuration for a pool that is meant to be accessible through the standard periphery), every unpermissioned user can bypass the per-user swap allowlist by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap()`, making the router the `sender` the extension sees. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the standard router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. The per-user curation is completely neutralised.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the LP-share recipient), which the caller supplies explicitly and which the pool uses for share accounting — the economically relevant identity is correctly bound. [4](#0-3) 

---

### Impact Explanation

Any user who is **not** on the swap allowlist can execute swaps against a curated pool by routing through `MetricOmmSimpleRouter` once the router is allowlisted. This breaks the core pool functionality the allowlist was deployed to enforce and allows unauthorized traders to interact with the pool's liquidity, potentially extracting value from LP positions or executing trades the pool admin explicitly intended to block. The impact is a direct loss of LP assets or broken core pool functionality, satisfying the Critical/High threshold.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router — a natural and expected step for any pool that is meant to be reachable through the standard periphery. The admin has no way to simultaneously (a) allow router-mediated swaps and (b) enforce per-user restrictions using the current extension design. Any pool that attempts both configurations is fully exposed. The trigger is an unprivileged public call to the router.

---

### Recommendation

The extension must gate the **ultimate user**, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` (the user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` as a proxy for the user**: For most swap flows the recipient is the user. The extension could check `allowedSwapper[pool][recipient]` in addition to or instead of `sender`. This is imperfect when recipient differs from the initiator.

3. **Separate router-level allowlisting from user-level allowlisting**: Introduce a two-layer check — the extension first verifies the router is an approved intermediary, then requires the router to attest (via signed `extensionData`) that the originating user is allowlisted.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; admin allowlists the router
// so that allowlisted users can use the standard periphery.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// alice is individually allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// eve is NOT allowlisted
// allowedSwapper[pool][eve] == false

// Eve routes through the public router instead of calling pool.swap() directly.
// router calls pool.swap() → msg.sender inside pool == address(router)
// _beforeSwap passes sender = address(router) to the extension
// extension checks allowedSwapper[pool][router] == true → passes
// Eve's swap executes successfully despite not being on the allowlist.
vm.startPrank(eve);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: false,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: eve,
        priceLimitX64: type(uint128).max,
        callbackData: "",
        extensionData: ""
    })
);
vm.stopPrank();
// Passes — eve swapped in a pool she was explicitly excluded from.
``` [3](#0-2) [5](#0-4) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
