### Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool. When `MetricOmmSimpleRouter` is the caller of `pool.swap()`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (the natural action to let their curated users trade through the supported periphery), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap()`, `msg.sender` is passed verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that same `sender` value to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user: [3](#0-2) 

The pool's NatSpec explicitly documents that `msg.sender` pays but need not equal `owner`, and that the router is a supported public entrypoint: [4](#0-3) 

**The broken invariant:** The allowlist is keyed on the router's address. A pool admin who wants their allowlisted users to be able to trade through the router must add `allowedSwapper[pool][router] = true`. Once the router is allowlisted, every user — including those never individually allowlisted — can call `router.exactInputSingle()` and the extension passes, because it sees `sender = router`.

This is structurally identical to the external report's root cause: the guard applies the threshold to the wrong operand. Here, the guard checks the wrong actor (router instead of user), so the protection is rendered ineffective for the exact path the protocol documents as supported.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties loses all access control the moment the router is allowlisted. Any unprivileged address can execute swaps on the restricted pool, draining LP value at oracle-determined prices without the pool admin's consent. This is a direct loss of LP principal and a complete curation failure on pools that rely on the allowlist as their primary safety boundary.

---

### Likelihood Explanation

The scenario is not hypothetical. The router is the documented, supported periphery path for end users. A pool admin who allowlists individual users and then wants those users to be able to use the router has no choice but to also allowlist the router address — there is no mechanism to say "allow alice through the router." The moment the admin takes that natural step, the allowlist is fully bypassed for all users. The trigger requires no special privilege: any EOA can call `router.exactInputSingle()`.

---

### Recommendation

The extension must check the original end user's address, not the intermediary router's address. Two sound approaches:

1. **Router forwards the initiator:** `MetricOmmSimpleRouter` encodes `msg.sender` (the real user) into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

2. **Pool-level initiator field:** Add an `initiator` field to the `beforeSwap` hook signature that the pool always sets to the original transaction sender (e.g., via `tx.origin` or a transient-storage initiator set at the top of `swap()`), and have the extension check `initiator` instead of `sender`.

The deposit allowlist correctly gates `owner` (the position beneficiary), not `sender` (the payer/operator): [5](#0-4) 

The swap allowlist should apply the same principle — gate the economically relevant actor (the user initiating the trade), not the intermediary contract.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin allowlists alice:
       swapExt.setAllowedToSwap(pool, alice, true)
3. Pool admin allowlists the router so alice can use it:
       swapExt.setAllowedToSwap(pool, router, true)
4. Charlie (never allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: charlie})
   → pool.swap(msg.sender=router) → _beforeSwap(sender=router)
   → allowedSwapper[pool][router] == true → PASSES
5. Charlie's swap executes on the curated pool.
   The allowlist provided zero protection against charlie.
```

The existing unit test `test_blocksSwapWhenSwapperNotAllowed` only exercises the direct-pool path (`vm.prank(address(pool)); extension.beforeSwap(swapper, ...)`), never the router-mediated path, so this bypass is untested: [6](#0-5)

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L111-113)
```text
  /// @notice Swap allowlist rejected `msg.sender`.
  /// @dev Only `swap` checks this when `SWAP_ALLOWLIST_PROVIDER` is set; `simulateSwapAndRevert` does not, so a passing simulation does not imply an allowed live swap.
  error NotAllowedToSwap();
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

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-30)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
