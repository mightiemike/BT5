Now I have all the information needed to make a determination. Let me trace the exact call path.

The call path is fully traceable and the bypass is real. Here is the determination:

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing a non-allowlisted payer to bypass the deposit gate via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates only on `owner`. Because `MetricOmmPoolLiquidityAdder` explicitly separates the position owner from the token payer (`msg.sender`), any caller can route through the adder with `owner = allowlistedAddress` and have their own (non-allowlisted) tokens accepted by the pool.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two identity arguments — `sender` (the pool's `msg.sender`, i.e. the adder contract acting on behalf of the real depositor) and `owner` (the position recipient). The implementation drops `sender` entirely and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, ...)   // sender unnamed/ignored
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (owner-explicit overload) accepts any non-zero `owner` from the caller and stores `msg.sender` as the token payer separately:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(address pool, address owner, ...)
    external payable override
{
    _validateOwner(owner);   // only checks owner != address(0)
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
    //                                                ^^^^^^^^^^
    //                                         payer = bob, owner = alice
}
``` [3](#0-2) 

The complete bypass path:

```
bob (non-allowlisted)
  → adder.addLiquidityExactShares(pool, alice, ...)   // owner=alice, payer=bob
    → pool.addLiquidity(alice, ...)                   // msg.sender=adder
      → _beforeAddLiquidity(sender=adder, owner=alice, ...)
        → extension.beforeAddLiquidity(adder, alice, ...)
          → allowedDepositor[pool][alice] == true  ✓  (gate passes)
      → LiquidityLib.addLiquidity(owner=alice, ...)   // alice gets LP shares
      → callback pulls tokens from bob               // bob's tokens enter pool
```

The extension's NatDoc states it "Gates `addLiquidity` by depositor address" — but the actual depositor (token source) is `sender`/payer, not `owner`. The mismatch is the root cause. [4](#0-3) 

---

### Impact Explanation

The pool admin's deposit allowlist is fully bypassed. Any non-allowlisted address can deposit tokens into a restricted pool by routing through the adder with `owner` set to any allowlisted address. The non-allowlisted address's tokens enter the pool and the allowlisted address receives the LP position. This is a direct admin-boundary break: an access-control mechanism intended to restrict who may deposit is circumvented by an unprivileged public path.

---

### Likelihood Explanation

The adder is a public, permissionless periphery contract. The owner-explicit overload of `addLiquidityExactShares` is part of the documented public API. No privileged access, special role, or malicious setup is required — only knowledge of one allowlisted address (which is on-chain readable via `allowedDepositor`). [5](#0-4) 

---

### Recommendation

Change `beforeAddLiquidity` to gate on `sender` (the actual token provider) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, ...)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

If the intent is to gate both the payer and the position owner, check both. The pool always passes `msg.sender` as `sender`, so for direct pool calls `sender == owner`; for adder-mediated calls `sender` is the adder (representing the real payer). If the adder should also be gated, the adder's `msg.sender` must be forwarded through `extensionData` and verified inside the extension.

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_nonAllowlistedBypassViaAdder() public {
    address alice = makeAddr("alice");
    address bob   = makeAddr("bob");

    // Pool admin allowlists only alice
    depositExtension.setAllowedToDeposit(address(pool), alice, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), bob));

    // Fund bob and approve the adder
    token0.mint(bob, 1_000 ether);
    token1.mint(bob, 1_000 ether);
    vm.startPrank(bob);
    token0.approve(address(adder), type(uint256).max);
    token1.approve(address(adder), type(uint256).max);

    // Bob deposits with owner=alice — should revert but does NOT
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);
    adder.addLiquidityExactShares(address(pool), alice, 1, d,
                                  type(uint256).max, type(uint256).max, "");
    vm.stopPrank();

    // Alice holds LP shares funded entirely by bob
    uint256 aliceShares = stateView.positionBinShares(address(pool), alice, 1, int8(4));
    assertGt(aliceShares, 0);   // passes — bypass confirmed
}
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L87-95)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);
```
