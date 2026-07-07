### Title
Two-Hop Rebasing Token Transfer Assumption Breaks Deposits — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`EndpointStorage.handleDepositTransfer` performs a two-hop token transfer: first pulling `amount` from the user into the `Endpoint` contract, then forwarding exactly `amount` onward to `Clearinghouse`. For rebasing tokens (e.g. stETH), the first hop delivers `amount − δ` (1–2 wei short due to share-based rounding), leaving the `Endpoint` with insufficient balance for the second hop. The second `safeTransfer` reverts, making deposits of any rebasing collateral token permanently impossible.

---

### Finding Description

`handleDepositTransfer` in `EndpointStorage.sol` is the single internal function that moves collateral from a depositor into the protocol:

```solidity
// EndpointStorage.sol L111-L119
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);               // L117
    safeTransferTo(token, address(clearinghouse), amount); // L118
}
``` [1](#0-0) 

For a rebasing token like stETH, `transferFrom(from, endpoint, amount)` at L117 succeeds and returns `true` (so `safeTransferFrom` does not revert), but the `Endpoint` contract actually receives `amount − δ` tokens (δ ∈ {1, 2} wei) because stETH internally rounds down share arithmetic. The `Endpoint` balance is now `amount − δ`.

L118 then calls `safeTransfer(clearinghouse, amount)` — attempting to forward the original `amount`. Because the `Endpoint` holds only `amount − δ`, this call reverts with an insufficient-balance error, rolling back the entire deposit transaction.

The same structural pattern exists in the withdrawal path: `Clearinghouse.handleWithdrawTransfer` transfers `amount` to `WithdrawPool`, then `BaseWithdrawPool.submitWithdrawal` calls `handleWithdrawTransfer(token, sendTo, amount)` forwarding the same `amount` out — again assuming no rounding loss occurred in the first hop. [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Any deposit of a rebasing ERC20 collateral token through `depositCollateral` / `depositCollateralWithReferral` permanently reverts at the second hop. No user can ever successfully deposit such a token; the protocol's collateral accounting for that product is entirely inaccessible. The same failure mode applies to withdrawals, where the `WithdrawPool` cannot forward the exact `amount` it received from `Clearinghouse`. No funds are permanently lost (the transaction reverts), but the deposit and withdrawal paths for rebasing tokens are completely broken.

---

### Likelihood Explanation

Likelihood is **medium** conditional on a rebasing token being listed as a supported collateral product. The `Endpoint` and `Clearinghouse` impose no restriction on token type — any address satisfying `IERC20Base` can be registered via `SpotEngine`. The `ContractOwner.wrapVaultAsset` path already demonstrates the protocol's intent to handle non-standard token mechanics. If an operator adds stETH or any share-accounting rebasing token as a product, every deposit and withdrawal for that product will revert deterministically, with no privileged action required from an attacker. [4](#0-3) 

---

### Recommendation

**Deposit fix**: Measure the `Endpoint`'s balance of `token` before and after `safeTransferFrom`, then forward only the balance difference to `Clearinghouse`:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(this));
    safeTransferFrom(token, from, amount);
    uint256 received = token.balanceOf(address(this)) - before;
    safeTransferTo(token, address(clearinghouse), received);
    // pass `received` (not `amount`) up to depositCollateral for credit
}
```

**Withdrawal fix**: In `Clearinghouse.handleWithdrawTransfer`, measure the `WithdrawPool`'s balance delta after the first hop and pass that delta to `submitWithdrawal`, or tolerate a small epsilon (e.g. 2 wei) in the amount forwarded to the end recipient.

---

### Proof of Concept

1. Operator registers stETH as a spot collateral product.
2. User calls `Endpoint.depositCollateral(subaccountName, stEthProductId, 1e18)`.
3. `handleDepositTransfer(stEth, user, 1e18)` is invoked.
4. `safeTransferFrom(stEth, user, 1e18)` succeeds; `Endpoint` receives `1e18 − 1` wei of stETH.
5. `safeTransferTo(stEth, clearinghouse, 1e18)` reverts — `Endpoint` balance is `1e18 − 1`.
6. Entire transaction reverts. Deposit is impossible for any stETH amount. [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/EndpointStorage.sol (L95-119)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }

    function safeTransferTo(
        IERC20Base token,
        address to,
        uint256 amount
    ) internal virtual {
        token.safeTransfer(to, amount);
    }

    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/ContractOwner.sol (L510-533)
```text
    function wrapVaultAsset(bytes32 subaccount, uint32 productId) external {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        if (directDepositV1 == address(0)) {
            directDepositV1 = createDirectDepositV1(subaccount);
        }

        address tokenAddr = spotEngine.getToken(productId);
        require(tokenAddr != address(0));

        address assetTokenAddr = IERC4626Base(tokenAddr).asset();
        require(assetTokenAddr != address(0));

        uint256 assetBalance = IERC20Base(assetTokenAddr).balanceOf(
            directDepositV1
        );
        if (IERC4626Base(tokenAddr).previewDeposit(assetBalance) != 0) {
            DirectDepositV1(directDepositV1).withdraw(
                IIERC20Base(assetTokenAddr)
            );
            IERC20Base assetToken = IERC20Base(assetTokenAddr);
            assetToken.approve(tokenAddr, 0);
            assetToken.approve(tokenAddr, assetBalance);
            IERC4626Base(tokenAddr).deposit(assetBalance, directDepositV1);
        }
```

**File:** core/contracts/Endpoint.sol (L103-120)
```text
    function depositCollateral(
        bytes12 subaccountName,
        uint32 productId,
        uint128 amount
    ) external {
        bytes32 subaccount = bytes32(
            abi.encodePacked(msg.sender, subaccountName)
        );
        require(
            isValidDepositAmount(subaccount, productId, amount),
            ERR_DEPOSIT_TOO_SMALL
        );
        depositCollateralWithReferral(
            subaccount,
            productId,
            amount,
            DEFAULT_REFERRAL_CODE
        );
```
