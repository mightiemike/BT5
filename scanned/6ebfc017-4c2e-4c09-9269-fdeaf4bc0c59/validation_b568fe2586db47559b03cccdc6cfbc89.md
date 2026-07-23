### Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing non-allowlisted depositors to bypass the deposit gate — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

---

### Summary

`DepositAllowlistExtension` is documented as "Gates `addLiquidity` by depositor address, per pool," but its `beforeAddLiquidity` hook silently discards the `sender` argument and checks `owner` instead. Because `MetricOmmPool.addLiquidity` explicitly permits `msg.sender ≠ owner`, any non-allowlisted address can call `addLiquidity(owner = allowlisted_address)` and pass the guard, bypassing the admin-configured deposit allowlist entirely.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two actor addresses: `sender` (the address that called `pool.addLiquidity()`, i.e., the token provider) and `owner` (the position owner). The implementation discards `sender` and gates on `owner`: [1](#0-0) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The pool's own NatSpec confirms that `addLiquidity` deliberately supports a different `msg.sender` and `owner`: [2](#0-1) 

> "Only `owner` may burn; `addLiquidity` may use a different `msg.sender` when `owner` is supplied, but removal is stricter."

The hook call site confirms `sender = msg.sender` (the actual caller) is passed as the first argument: [3](#0-2) 

Because `sender` is discarded, the guard never evaluates the actual depositor. A non-allowlisted address `bob` can call `pool.addLiquidity(owner = alice)` where `alice` is allowlisted; the extension checks `allowedDepositor[pool][alice]` → `true` → the call proceeds. Bob's tokens are consumed by the pool callback and credited to Alice's position. Bob cannot recover them (`removeLiquidity` enforces `msg.sender == owner`), but the allowlist is bypassed.

The `SwapAllowlistExtension`, by contrast, correctly checks `sender` (the swap initiator) and discards `recipient`: [4](#0-3) 

The asymmetry between the two extensions confirms the deposit extension checks the wrong actor.

---

### Impact Explanation

The deposit allowlist is an admin-configured access-control boundary. Bypassing it via an unprivileged path is an admin-boundary break under the allowed impact gate. Concretely:

- Any non-allowlisted address can add liquidity to a pool that is supposed to be restricted (e.g., KYC-gated, whitelist-only LP pools), undermining the pool admin's intended access policy.
- The allowlist is rendered ineffective as a depositor restriction: the guard passes whenever *any* allowlisted address is named as `owner`, regardless of who actually provides the tokens.
- Conversely, an allowlisted depositor who specifies a non-allowlisted `owner` is incorrectly blocked, breaking the intended liquidity flow for legitimate users.

---

### Likelihood Explanation

Exploitation requires no special privileges. Any address can call `pool.addLiquidity` with `owner` set to any allowlisted address discoverable from on-chain `AllowedToDepositSet` events. The attacker bears a token cost (tokens are locked in the owner's position), making this primarily a compliance/access-control break rather than a profit-driven attack, but the bypass is unconditionally reachable by any caller.

---

### Recommendation

Check `sender` (the actual depositor/caller) instead of `owner` (the position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

If the intended semantic is to gate by position owner (not caller), the contract's NatSpec and `IDepositAllowlistExtension` interface must be updated to document that distinction clearly, and the `setAllowedToDeposit` / `isAllowedToDeposit` APIs renamed accordingly to avoid operator misconfiguration.

---

### Proof of Concept

```
Setup:
  pool admin deploys pool with DepositAllowlistExtension
  admin calls extension.setAllowedToDeposit(pool, alice, true)
  bob is NOT allowlisted

Attack:
  bob calls pool.addLiquidity(owner=alice, salt=0, deltas=..., callbackData=..., extensionData=...)

Pool calls extension:
  extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, 0, deltas, "")
  → checks allowedDepositor[pool][alice] == true  ✓
  → does NOT check allowedDepositor[pool][bob]
  → returns selector, no revert

Result:
  bob's tokens are pulled via the pool callback and credited to alice's position
  bob bypassed the deposit allowlist
  alice can call removeLiquidity to recover the tokens
```

The existing unit test `test_revertsWhenDepositorNotAllowed` only tests the case where `owner` is not allowlisted; it does not test the case where `sender ≠ owner` and `owner` is allowlisted but `sender` is not, leaving the bypass uncovered. [5](#0-4)

### Citations

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L89-91)
```text
  /// @notice `removeLiquidity` caller is not the position owner.
  /// @dev Only `owner` may burn; `addLiquidity` may use a different `msg.sender` when `owner` is supplied, but removal is stricter.
  error NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L121-129)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_REMOVE_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeRemoveLiquidity, (sender, owner, salt, deltas, extensionData))
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

**File:** metric-periphery/test/extensions/DepositAllowlistSubExtension.t.sol (L27-41)
```text
  function test_revertsWhenDepositorNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }

  function test_passesWhenDepositorAllowed() public {
    vm.prank(admin);
    extension.setAllowedToDeposit(address(pool), depositor, true);

    vm.prank(address(pool));
    LiquidityDelta memory emptyDelta = LiquidityDelta({binIdxs: new int256[](0), shares: new uint256[](0)});
    extension.beforeAddLiquidity(address(0), depositor, 0, emptyDelta, "");
  }
```
