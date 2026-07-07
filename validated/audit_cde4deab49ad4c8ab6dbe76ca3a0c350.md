### Title
Unsafe `transferFrom()` in `replaceUsdcEWithUsdc()` Does Not Check Return Value, Enabling Silent Failure and usdcE Drain — (`core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` directly without checking the return value. The `ERC20Helper.safeTransferFrom` wrapper is available via `using ERC20Helper for IERC20Base` but is not used. If the `transferFrom` silently returns `false` instead of reverting, the function continues to withdraw usdcE from the DDA and transfer it to the caller — who provided no USDC — resulting in a direct loss of usdcE from the DDA.

---

### Finding Description

`ContractOwner` imports and applies `ERC20Helper` as a library extension on `IERC20Base`: [1](#0-0) 

`ERC20Helper` provides a `safeTransferFrom` that uses a low-level `.call()` and checks both the call success flag and the decoded boolean return value: [2](#0-1) 

Despite this safe wrapper being available, `replaceUsdcEWithUsdc()` calls the raw `transferFrom` directly on line 616: [3](#0-2) 

The raw call `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` at line 616 goes through the ABI-encoded interface. If the USDC token returns `false` (non-reverting failure), Solidity silently discards the boolean — the call is considered successful at the EVM level. Execution then proceeds to:

1. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulls all usdcE out of the DDA into `ContractOwner`.
2. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends that usdcE to the caller.

The caller receives usdcE without having transferred any USDC. The inconsistency is stark: the very next line (618) correctly uses `safeTransfer` for the usdcE leg of the same swap, while the USDC leg on line 616 uses the unsafe path. [4](#0-3) 

---

### Impact Explanation

If the USDC token on chain 57073 (Ink mainnet) returns `false` on a failed `transferFrom` rather than reverting — a behavior exhibited by some non-standard ERC20 deployments — any caller can:

- Invoke `replaceUsdcEWithUsdc(subaccount)` for any DDA that holds usdcE.
- Receive the full usdcE balance of that DDA without providing any USDC.
- Repeat for every DDA with a usdcE balance.

This is a direct, concrete loss of user funds held in DDA contracts.

---

### Likelihood Explanation

The function is `external` with no `onlyOwner` or `onlyDeployer` modifier — the only gate is `require(block.chainid == 57073)`. Any EOA or contract on Ink mainnet can call it. The USDC address `0x2D270e6886d130D724215A266106e6832161EAEd` is a specific deployment whose exact return-value behavior on failure determines exploitability. Even if this particular deployment reverts on failure (making the silent-false path unreachable today), the unchecked pattern is a latent defect: a token upgrade or a future product addition using a non-standard token would immediately expose the same code path.

---

### Recommendation

Replace the raw `transferFrom` call with the already-available `safeTransferFrom` from `ERC20Helper`:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` in `ERC20Helper` uses a low-level `.call()` and asserts `success && (data.length == 0 || abi.decode(data, (bool)))`, handling both void-returning tokens and false-returning tokens correctly. [2](#0-1) 

---

### Proof of Concept

1. Deploy or identify a DDA (`directDepositV1Address[subaccount]`) on chain 57073 that holds a non-zero usdcE balance.
2. Deploy a mock USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (or simulate via fork) whose `transferFrom` returns `false` instead of reverting.
3. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from any EOA with zero USDC balance/allowance.
4. Observe: `transferFrom` returns `false`, return value is ignored, execution continues, `withdraw(usdcE)` drains the DDA, `safeTransfer` sends the full usdcE balance to the caller.
5. Caller holds usdcE; DDA holds nothing; no USDC was ever transferred. [3](#0-2)

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
