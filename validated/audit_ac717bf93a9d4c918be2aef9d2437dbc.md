### Title
Unchecked `approve` Return Value in `creditDeposit()` Enables Silent Deposit Failure - (File: core/contracts/DirectDepositV1.sol)

---

### Summary
`DirectDepositV1.creditDeposit()` calls `token.approve(address(endpoint), balance)` at line 92 without capturing or checking the returned `bool`. For tokens whose `approve` returns `false` without reverting, the deposit proceeds with zero allowance. The endpoint's subsequent `transferFrom` will fail; if the endpoint does not propagate that revert, the subaccount is credited without the tokens being transferred, corrupting protocol collateral accounting.

---

### Finding Description
`creditDeposit()` iterates over all spot product IDs and, for each token held by the DDA, calls `token.approve(address(endpoint), balance)` followed immediately by `endpoint.depositCollateralWithReferral(subaccount, productId, uint128(balance), "-1")`. [1](#0-0) 

The `IIERC20Base` interface explicitly declares `approve` as returning `bool`: [2](#0-1) 

Yet at line 92 the return value is silently discarded. This directly contradicts the codebase's own defensive pattern: the `safeTransfer` helper in the same file uses a low-level `.call` and `require`s both the call success and the decoded boolean: [3](#0-2) 

The same safe pattern is applied to `transfer` and `transferFrom` in `ERC20Helper`: [4](#0-3) 

`approve` is the only token operation in this contract that skips this check. For a token whose `approve` returns `false` without reverting (a documented non-standard ERC20 pattern), execution falls through to `depositCollateralWithReferral` with zero actual allowance granted to the endpoint.

---

### Impact Explanation
The endpoint's `depositCollateralWithReferral` will attempt to pull `balance` tokens from the DDA via `transferFrom`. With zero allowance, that transfer fails. Two outcomes are possible:

- **If the endpoint reverts on `transferFrom` failure**: `creditDeposit()` reverts entirely — the deposit is blocked (DoS on deposit for that token).
- **If the endpoint does not revert on `transferFrom` failure**: the subaccount's on-chain balance is credited with `balance` tokens that were never actually transferred out of the DDA, directly corrupting the protocol's collateral accounting and enabling undercollateralized positions.

The second outcome is the critical one: the DDA retains the tokens while the subaccount is credited, breaking the 1:1 collateral invariant.

---

### Likelihood Explanation
`creditDeposit()` is `external` with no access control modifier: [5](#0-4) 

It is also reachable via `ContractOwner.creditDepositV1()`, which is equally unrestricted: [6](#0-5) 

Any unprivileged user can trigger this path. The trigger requires a listed spot token whose `approve` returns `false` without reverting. Likelihood is low-to-medium depending on which tokens are listed as spot products.

---

### Recommendation
Replace the bare `token.approve(...)` call with a checked pattern mirroring the existing `safeTransfer` helper:

```solidity
// In DirectDepositV1.sol, replace line 92:
- token.approve(address(endpoint), balance);
+ (bool ok, bytes memory data) = address(token).call(
+     abi.encodeWithSelector(IIERC20Base.approve.selector, address(endpoint), balance)
+ );
+ require(ok && (data.length == 0 || abi.decode(data, (bool))), "Approve failed");
```

Or introduce a `safeApprove` internal function in `DirectDepositV1` analogous to the existing `safeTransfer`.

---

### Proof of Concept
1. List a token whose `approve(spender, amount)` returns `false` without reverting as a spot product in the engine.
2. Send some of that token to the DDA address.
3. Call `creditDeposit()` (or `ContractOwner.creditDepositV1(subaccount)`) from any EOA.
4. `token.approve(address(endpoint), balance)` returns `false`; the return value is ignored and execution continues.
5. `endpoint.depositCollateralWithReferral(subaccount, productId, balance, "-1")` is called with zero allowance on the endpoint.
6. If the endpoint silently swallows the `transferFrom` failure, the subaccount is credited `balance` tokens while the DDA's token balance is unchanged — collateral accounting is corrupted.

### Citations

**File:** core/contracts/DirectDepositV1.sol (L11-11)
```text
    function approve(address spender, uint256 amount) external returns (bool);
```

**File:** core/contracts/DirectDepositV1.sol (L69-81)
```text
    function safeTransfer(
        IIERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IIERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            "Transfer failed"
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-83)
```text
    function creditDeposit() external {
```

**File:** core/contracts/DirectDepositV1.sol (L91-99)
```text
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/ContractOwner.sol (L502-508)
```text
    function creditDepositV1(bytes32 subaccount) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }
        DirectDepositV1(directDepositV1).creditDeposit();
    }
```
