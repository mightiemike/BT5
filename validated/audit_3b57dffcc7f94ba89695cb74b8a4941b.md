### Title
Raw `transferFrom` on Non-Compliant ERC20 Permanently Blocks USDC-E Migration — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` directly through the typed interface. If the USDC token at the hardcoded Ink-chain address does not return a value on `transferFrom`, Solidity's ABI decoder reverts on the empty `RETURNDATASIZE`, permanently blocking the USDC-E → USDC migration path for every affected subaccount. The contract already imports and uses `ERC20Helper.safeTransferFrom` for this exact purpose but omits it here.

---

### Finding Description

`ContractOwner` declares `using ERC20Helper for IERC20Base` and uses `safeTransfer` in two other places in the same file (lines 618, 642). However, `replaceUsdcEWithUsdc` at line 616 calls the raw interface method:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

`IERC20Base.transferFrom` is declared as returning `bool`. Under Solidity ≥0.8, if the token returns no data, the ABI decoder checks `RETURNDATASIZE` against the expected 32-byte `bool` and reverts. `ERC20Helper.safeTransferFrom` avoids this by using a low-level `.call` and accepting `data.length == 0` as a success condition, but it is not used here.

The inconsistency is visible within the same function body: the very next line (618) calls `IERC20Base(usdcE).safeTransfer(...)` using the safe wrapper, while the `transferFrom` two lines earlier does not. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

`replaceUsdcEWithUsdc` is the only on-chain path to migrate USDC-E balances held in `DirectDepositV1` addresses to USDC. If the raw `transferFrom` reverts due to a non-compliant return value, the migration is permanently blocked for every subaccount that has USDC-E in its DDA. The USDC-E tokens remain locked in the DDA with no alternative withdrawal path callable by the user, because `withdrawFromDirectDepositV1` is `onlyOwner`. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

The function is restricted to `block.chainid == 57073` (Ink chain) but has no caller access control — any user can invoke it. The USDC address `0x2D270e6886d130D724215A266106e6832161EAEd` is a chain-specific deployment whose compliance with the `bool` return convention cannot be assumed. Bridged or wrapped stablecoins on newer chains frequently omit the return value. The same codebase already treats this as a real risk for `transfer` (hence `safeTransfer` everywhere else), making the omission here a concrete gap rather than a theoretical one. [5](#0-4) 

---

### Recommendation

Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, which is already imported and in scope via `using ERC20Helper for IERC20Base`:

```solidity
// Before (line 616)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

Apply the same fix to the raw `approve` call in `DirectDepositV1.creditDeposit` (line 92) and `ContractOwner.depositInsurance` / `wrapVaultAsset` (lines 254, 530–531), which share the same class of risk. [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. A USDC-E holder's DDA has `balance > 0` of USDC-E.
2. Any caller invokes `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on Ink chain (chainid 57073).
3. Execution reaches line 616: `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)`.
4. The USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` executes the transfer successfully but returns no data.
5. Solidity's ABI decoder expects 32 bytes for the `bool` return; `RETURNDATASIZE == 0` causes a revert.
6. The migration reverts. The USDC-E remains in the DDA. The user cannot call `withdrawFromDirectDepositV1` (it is `onlyOwner`). Funds are effectively locked until an owner-level intervention. [3](#0-2)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/ContractOwner.sol (L622-647)
```text
    function withdrawFromDirectDepositV1(bytes32 subaccount, address token)
        external
        onlyOwner
    {
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        if (token == address(0)) {
            uint256 preBalance = address(this).balance;
            DirectDepositV1(directDepositV1).withdrawNative();
            uint256 postBalance = address(this).balance;
            require(postBalance > preBalance, "empty");
            (bool success, ) = msg.sender.call{value: postBalance - preBalance}(
                ""
            );
            require(success, "xfer");
        } else {
            uint256 preBalance = IERC20Base(token).balanceOf(address(this));
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(token));
            uint256 postBalance = IERC20Base(token).balanceOf(address(this));
            require(postBalance > preBalance, "empty");
            IERC20Base(token).safeTransfer(
                msg.sender,
                postBalance - preBalance
            );
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L90-93)
```text
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
```
