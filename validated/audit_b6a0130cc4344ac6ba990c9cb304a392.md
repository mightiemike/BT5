The call path is fully traceable. Here is the complete analysis:

**Call chain:**
1. Attacker B calls `addLiquidityExactShares(pool, A, salt, deltas, ...)` on `MetricOmmPoolLiquidityAdder`
2. `_validateOwner(A)` only checks `A != address(0)` — no check that `owner == msg.sender`
3. `_addLiquidity(pool, positionOwner=A, ..., payer=B, ...)` is called; B is stored as payer in transient context
4. Pool's `addLiquidity` is called with `owner=A`, which calls `_beforeAddLiquidity(sender=LiquidityAdder, owner=A, ...)`
5. `DepositAllowlistExtension.beforeAddLiquidity` receives `(sender=LiquidityAdder, owner=A, ...)` but **ignores the first `address` parameter entirely** and checks only `allowedDepositor[msg.sender][owner]` — i.e., `allowedDepositor[pool][A]` — which is `true`
6. Shares are minted under position key `(pool, A, salt)`; callback pulls tokens from B

The `sender` (the actual depositing entity) is silently discarded. The extension's own mapping is named `allowedDepositor` and its setter is `setAllowedToDeposit`, making the intent clear: restrict who deposits. But the check is on `owner` (who receives shares), not `sender` (who pays tokens).

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently ignores the `sender` parameter and gates only on `owner`. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with no identity check, any non-allowlisted address can deposit into a curated pool by naming an allowlisted address as `owner`.

### Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` has the signature `(address /*sender*/, address owner, ...)` but its guard reads:

```solidity
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [1](#0-0) 

The `sender` argument — which is `msg.sender` of the pool's `addLiquidity` call, i.e. the `MetricOmmPoolLiquidityAdder` acting on behalf of the real caller — is completely ignored. The check only validates `owner`, the position recipient.

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the overload with an explicit `owner` parameter) performs no identity check between `owner` and `msg.sender`:

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [2](#0-1) 

The payer stored in transient context is always `msg.sender` (attacker B), while `positionOwner` passed to the pool is the attacker-supplied allowlisted address A: [3](#0-2) 

The pool passes `msg.sender` (the adder contract) as `sender` to the extension hook: [4](#0-3) 

The extension hook encodes both `sender` and `owner` in the call: [5](#0-4) 

But `DepositAllowlistExtension` discards `sender` entirely and only checks `owner`.

### Impact Explanation
The deposit allowlist — the primary curation mechanism for private/permissioned pools — is fully bypassed. Any address can deposit into a pool that is supposed to accept only allowlisted depositors, simply by supplying an allowlisted address as `owner`. The attacker forfeits their tokens (they cannot withdraw from A's position key), but:

- The pool's curation invariant is broken: non-allowlisted capital enters the pool.
- The attacker can grief an allowlisted user A by inflating A's position (adding shares to A's `(pool, A, salt)` key without A's consent), forcing A to manage unexpected exposure.
- Pool admins who rely on the allowlist for regulatory, compliance, or strategy-isolation reasons have no effective control over who deposits.

`removeLiquidity` enforces `msg.sender == owner`, so only A can withdraw the minted shares — the attacker's tokens are permanently locked in A's position unless A chooses to remove them. [6](#0-5) 

### Likelihood Explanation
The attack requires only a public call to `addLiquidityExactShares` with a known allowlisted address. No privileged access, no special setup, no flash loan. Any address that can observe the allowlist state (public mapping) can execute this immediately.

### Recommendation
In `DepositAllowlistExtension.beforeAddLiquidity`, check `sender` (the actual depositing entity) rather than `owner` (the position recipient):

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both who pays *and* who holds positions, check both `sender` and `owner`. The `_validateOwner` in `MetricOmmPoolLiquidityAdder` should also enforce `owner == msg.sender` unless delegation is explicitly intended.

### Proof of Concept
1. Deploy a pool with `DepositAllowlistExtension`; allowlist only address A (`setAllowedToDeposit(pool, A, true)`).
2. As attacker B (not allowlisted), approve `MetricOmmPoolLiquidityAdder` for token spending.
3. Call `addLiquidityExactShares(pool, A, salt, deltas, max0, max1, "")` from B.
4. `_validateOwner(A)` passes (A ≠ 0). Payer stored as B.
5. Pool calls `beforeAddLiquidity(sender=adder, owner=A, ...)`. Extension checks `allowedDepositor[pool][A] == true` → passes.
6. Shares minted under `(pool, A, salt)`; callback pulls tokens from B.
7. Assert: B's token balance decreased; A's position shares increased; B holds no shares.

The allowlist is bypassed: B (non-allowlisted) deposited into a curated pool.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```
