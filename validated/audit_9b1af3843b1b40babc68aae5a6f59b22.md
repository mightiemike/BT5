### Title
Unvalidated `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables Silent Failure and usdcE Drain - (File: `core/contracts/ContractOwner.sol`)

### Summary
`ContractOwner.replaceUsdcEWithUsdc()` calls `IERC20Base(usdc).transferFrom(...)` directly without validating its return value, while the outbound transfer on the very next line correctly uses `safeTransfer`. If the USDC `transferFrom` returns `false` instead of reverting, execution continues, withdrawing usdcE from the `DirectDepositV1` contract and sending it to the caller — who provided no USDC.

### Finding Description
`replaceUsdcEWithUsdc` is an unrestricted external function (no `onlyOwner` or similar modifier) that performs a three-step token swap:

1. Pull USDC from `msg.sender` into `directDepositV1` — **raw `transferFrom`, return value ignored** (line 616)
2. Withdraw usdcE from `directDepositV1` into `ContractOwner` (line 617)
3. Send usdcE to `msg.sender` — correctly uses `safeTransfer` (line 618) [1](#0-0) 

The inconsistency is explicit: the inbound leg uses raw `transferFrom` while the outbound leg uses `safeTransfer`. The `ERC20Helper.safeTransferFrom` wrapper exists in the codebase and is used throughout `EndpointStorage` and `BaseWithdrawPool`, but is not applied here. [2](#0-1) 

### Impact Explanation
If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` (chain 57073 / Ink) returns `false` on a failed `transferFrom` rather than reverting — a valid ERC20 behavior — the function does not revert. Steps 2 and 3 still execute: usdcE is drained from `directDepositV1` and transferred to the caller at no cost. The corrupted asset delta is the full `usdcE` balance held in any `directDepositV1` instance on that chain.

### Likelihood Explanation
The function is callable by any unprivileged address. The only preconditions are: (a) chain ID is 57073, (b) a `directDepositV1` address exists for the target `subaccount`, and (c) it holds a non-zero usdcE balance. The USDC deployment on Ink is not the canonical Circle USDC and its exact revert-vs-return-false behavior should not be assumed. The inconsistency with `safeTransfer` on line 618 confirms the inbound path was not intentionally left unguarded. [3](#0-2) 

### Recommendation
Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with how all other token pulls in the codebase are handled:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [4](#0-3) 

### Proof of Concept
1. Attacker identifies a `subaccount` whose `directDepositV1` holds `N` usdcE tokens on chain 57073.
2. Attacker calls `replaceUsdcEWithUsdc(subaccount)` with zero USDC allowance granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, N)` returns `false` (insufficient allowance) — no revert.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes, pulling `N` usdcE into `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, N)` executes, sending `N` usdcE to the attacker.
6. Attacker receives `N` usdcE having deposited zero USDC. [5](#0-4)

### Citations

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

**File:** core/contracts/EndpointStorage.sol (L95-101)
```text
    function safeTransferFrom(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal virtual {
        token.safeTransferFrom(from, address(this), amount);
    }
```
